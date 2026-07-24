#!/usr/bin/env python3
"""Fail-closed audit for the Polaris image-publication checkpoint."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Optional


POLARIS_SOURCE = Path("bootstrap/polaris/v1.6.0/source.json")
POLARIS_CONTRACT = Path("bootstrap/polaris/v1.6.0/trusted-build-contract.json")
POLARIS_ADMISSION = Path("bootstrap/polaris/v1.6.0/admission.json")
POLARIS_RELEASE_EVIDENCE = Path(
    "bootstrap/polaris/v1.6.0/release-evidence.json"
)
POLARIS_ATOMIC_ADMISSION = Path(
    "bootstrap/polaris/v1.6.0/atomic-admission.json"
)
POLARIS_ADMIN_BUILD_INPUTS_CONTRACT = Path(
    "bootstrap/polaris/v1.6.0/admin-build-inputs-contract.json"
)
POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE = Path(
    "bootstrap/polaris/v1.6.0/admin-build-inputs-evidence"
)
POLARIS_ADMIN_BUILD_INPUTS_VERIFIER = Path(
    "scripts/verify_polaris_admin_build_inputs.py"
)
POLARIS_ADMIN_IMAGE_CONTRACT = Path(
    "bootstrap/polaris/v1.6.0/admin-image-contract.json"
)
POLARIS_ADMIN_ADMISSION = Path(
    "bootstrap/polaris/v1.6.0/admin-admission.json"
)
POLARIS_ADMIN_IMAGE_CONTAINERFILE = Path(
    "bootstrap/polaris/v1.6.0/Containerfile.admin"
)
POLARIS_ADMIN_IMAGE_VERIFIER = Path("scripts/verify_polaris_admin_image.py")
POLARIS_ADMIN_IMAGE_EVIDENCE = Path(
    "bootstrap/polaris/v1.6.0/admin-image-evidence"
)
POLARIS_ADMIN_ADMISSION_EVIDENCE = Path(
    "security/evidence/polaris-admin-v1.6.0"
)
POLARIS_ADMIN_RELEASE_EVIDENCE = Path(
    "bootstrap/polaris/v1.6.0/admin-release-evidence.json"
)
POLARIS_CONTAINERFILE = Path("bootstrap/polaris/v1.6.0/Containerfile")
POLARIS_SOURCE_OVERLAY = Path(
    "bootstrap/polaris/v1.6.0/patches/"
    "0001-shirokuma-bounded-runtime.patch"
)
POLARIS_IMAGE_WORKFLOW = Path(".github/workflows/polaris-arm64.yml")
POLARIS_KEY = Path(
    "bootstrap/polaris/v1.6.0/apache-polaris-release-signing-key.asc"
)
POLARIS_EVIDENCE = Path("bootstrap/polaris/v1.6.0/evidence")
POLARIS_IMAGE_EVIDENCE = Path(
    "bootstrap/polaris/v1.6.0/image-evidence"
)
POSTGRES_ADMISSION = Path("bootstrap/postgresql/v18.4/admission.json")
POSTGRES_EVIDENCE = Path("bootstrap/postgresql/v18.4/evidence")
RESIDENT_LEDGER = Path("security/resident-images.json")
ATOMIC_EVIDENCE = Path(
    "security/evidence/polaris-v1.6.0-postgresql-v18.4"
)
ATOMIC_EVIDENCE_MANIFEST = ATOMIC_EVIDENCE / "evidence.sha256"
ATOMIC_SUPPLY_CHAIN = ATOMIC_EVIDENCE / "supply-chain.json"

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
POLARIS_KEY_FINGERPRINT_GROUPED = (
    "F2EE EB06 110B EE13 97EC 74CB B896 0FF5 2D9B 1312"
)
POLARIS_KEY_UID = (
    "Apache Polaris Automated Release Signing <private@polaris.apache.org>"
)
POLARIS_COMMIT = "dd306009d81a0e15adafe9dcd7d1c6d04d326f34"
POLARIS_TREE = "1ad42f42aaebfa767b66a37f522a6c8d6693d841"
POLARIS_SOURCE_SHA256 = (
    "7d14b606dd756f501644190c10deb64a1e046d46faacd0f76f92501ccd5185bb"
)
POLARIS_CONTRACT_SHA256 = (
    "cacd353f81996f5b04965fb3213cdecb3ebfbdc648ebbb3a8a90609412fa59ac"
)
POLARIS_ADMIN_BUILD_INPUTS_CONTRACT_SHA256 = (
    "6d56a2b086591f746bf272ff9388529013780b36950834e5233e41c34b16e400"
)
POLARIS_ADMIN_BUILD_INPUTS_VERIFIER_SHA256 = (
    "5e153aacecaec7c313d9caba5b38ef65ff92f7eed25746e879222a4cdf441a42"
)
POLARIS_ADMIN_IMAGE_CONTRACT_SHA256 = (
    "c5aacf801c54413fcc2e8b7a460527f56dabcc65ef560d1ab879e3c58c33c862"
)
POLARIS_ADMIN_ADMISSION_SHA256 = (
    "99d1fc36c2960584be7b529c9601e6667deae842f2eb16fd36c949b7c3efaa14"
)
POLARIS_ADMIN_IMAGE_CONTAINERFILE_SHA256 = (
    "cecd7e40f0bd3b2f5b0de90233677772c0c55c745f4f4cc975eda83b42f40112"
)
POLARIS_ADMIN_IMAGE_VERIFIER_SHA256 = (
    "e7cf968266a109169c73323c91e407dc6638b784aab88dc82b2fcbc679d4d672"
)
POLARIS_ADMIN_RELEASE_EVIDENCE_SHA256 = (
    "8d3f4b4550e4cebbd7e9d83d07376c7b5ba5f0013a49a044624d914d70df7c10"
)
POLARIS_ADMIN_IMAGE_EVIDENCE_MANIFEST_SHA256 = (
    "f1290ccf0fff852fb965d46ab55c12623ce15e36e15b4bbeb6627999bf11a97f"
)
REVIEWED_POLARIS_CONTRACT_SHA256 = (
    "db27ec5ebf627ef1772c898614d5f206a2a3affc67007ee29221c525ab8fd3d6"
)
REVIEWED_POLARIS_ADMISSION_SHA256 = (
    "78deb90ab1aaede0ff384e2240d52d877678916af038b7dedf802f62da893369"
)
POLARIS_DEPENDENCY_REVIEW_MERGE = "b12593f27ae4e6ec8b64865f9b6b0bbf114ec654"
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
IMAGE_RUNTIME_INDEX = (
    "docker.io/library/amazoncorretto@sha256:"
    "d3a3476c19cbe37b2e3e46a2116ff197ab37c7072baad55ee0ad07f3b97e8d02"
)
IMAGE_RUNTIME_ARM64 = (
    "docker.io/library/amazoncorretto@sha256:"
    "ba1fe4a3fd4c6b70360183fccd1f0a168c3ea6f73709e8f81945cb9087431ff2"
)
POLARIS_CONTAINERFILE_SHA256 = (
    "e2aea29a93ac4369fc558c2161966fa48e3061238cc41ae849788f8e8e4cfea8"
)
POLARIS_SOURCE_OVERLAY_SHA256 = (
    "c5739a49baac0d08e6cf71a4dabd06141618f9474702e6c24fd1bb7f22571f48"
)
POLARIS_IMAGE_WORKFLOW_SHA256 = (
    "50e0a0407cd65accdd573cf85637d7ccec97774aeecf569d8b5c9acc6b502b5d"
)
POLARIS_RELEASE_EVIDENCE_SHA256 = (
    "2e3ca5a8245669ccc818f2a22a8be16e901a9b7b73b5eb71237e8c6affdd6f69"
)
POLARIS_ATOMIC_ADMISSION_SHA256 = (
    "cc7ff13fb87bec48537e5944d4820c26fd81c0b5bd215095da8df2ecfff5d4d3"
)
ATOMIC_EVIDENCE_MANIFEST_SHA256 = (
    "fbf7c8bd7540000ac038a6afe12cc01d7bd1364d30202e6a98b7e85867fbe1d8"
)
ATOMIC_EVIDENCE_MANIFEST_SIZE = 634
ATOMIC_EVIDENCE_PRIMARY = {
    "anonymous-preflight.json": (
        "75cc42cb081bebcf7700c76a7c546b9ab0e8ac89202d7e8fef0ccc763f79fcec"
    ),
    "polaris-1.6.0-arm64.cdx.json": (
        "b724a92c7d686bdc5a931aa455ee5d3d66e650e371ed602804116adade12bc30"
    ),
    "polaris-trivy.json": (
        "1ee7994db68a5ad999fc1604b8e0902add3f492b97c12c88f6c3fbbf3a3f098e"
    ),
    "postgresql-18.4-arm64.cdx.json": (
        "f07cc69d805de9161cad8bec49153b3f8908ec78b4ee21a0655736f43ef32ed6"
    ),
    "postgresql-trivy-sbom.json": (
        "66fc88304a642c6522c49ff6b76e5ab313712fcdc4c19d838df13868f22f01ab"
    ),
    "postgresql-trivy.json": (
        "280cb840d27662f9131f6f0907ff5939604fb22cd5257996fd0390ed96e5bf26"
    ),
    "trivy-version.json": (
        "37a6fe2034f88374927f7303385457b2222fc73f0cff0f0bcc53a333fa9df298"
    ),
}
POLARIS_IMAGE_DIGEST = (
    "sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e"
)
POLARIS_IMAGE_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-polaris@" + POLARIS_IMAGE_DIGEST
)
POLARIS_IMAGE_TRUSTED_TAG = (
    "ghcr.io/tommykammy/shirokuma-polaris:1.6.0-arm64"
)
POLARIS_IMAGE_PUBLISHER_SOURCE_SHA = (
    "706575ba3f21987033a29b6d21367981e9c54e3e"
)
POLARIS_IMAGE_PUBLISHER_RUN_ID = "29711984394"
POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT = "1"
POLARIS_IMAGE_CANDIDATE_TAG = (
    "ghcr.io/tommykammy/shirokuma-polaris:"
    "1.6.0-29711984394-1"
)
POLARIS_IMAGE_CREATED = "2026-07-20T02:15:01.596807+00:00"
POLARIS_IMAGE_PROMOTION_COMPLETED_AT = "2026-07-20T02:15:45.689352+00:00"
POLARIS_IMAGE_SLSA_PROVENANCE = (
    "https://github.com/TommyKammy/Shirokuma/attestations/36078805"
)
POLARIS_IMAGE_PUBLISHER_IDENTITY = (
    "https://github.com/TommyKammy/Shirokuma/"
    ".github/workflows/polaris-arm64.yml@refs/heads/main"
)
POLARIS_IMAGE_PUBLISHER_ISSUER = (
    "https://token.actions.githubusercontent.com"
)
POLARIS_IMAGE_PUBLISHER_REPOSITORY = "TommyKammy/Shirokuma"
POLARIS_IMAGE_PUBLISHER_REF = "refs/heads/main"
POLARIS_IMAGE_PUBLISHER_TRIGGER = "push"
POLARIS_IMAGE_PUBLISHER_CONTRACT_SHA256 = (
    "8625191c6a186880d7ec7a596667b047881170e987527c5987a5ee87285b83f8"
)
POLARIS_IMAGE_PUBLISHER_ADMISSION_SHA256 = (
    "9e45b73b94d6b988e368826a532537f5965467d614116c8fee88f30ea4e01b2d"
)
POLARIS_IMAGE_EVIDENCE_MANIFEST_SHA256 = (
    "5aac29c01f28f410a6e564b87cd2748eeef1204341a8373934491d29b21564d7"
)
POLARIS_IMAGE_EVIDENCE_MANIFEST_SIZE = 2_910
POLARIS_CANDIDATE_EVIDENCE_REQUIRED = [
    "anonymous-image-manifest.json",
    "builder-metadata.json",
    "build-context.sha256",
    "build-input.json",
    "dependency-input.json",
    "offline-build.json",
    "source-authentication.json",
    "cosign-signature-bundle.json",
    "cosign-verify.json",
    "health-ready.json",
    "image-config.json",
    "image-manifest.json",
    "polaris-1.6.0-arm64.cdx.json",
    "registry-signature-bundles.jsonl",
    "rekor-entry.json",
    "runtime-container-inspect.json",
    "runtime-base-java-version.txt",
    "runtime-base-manifest.json",
    "runtime-smoke.json",
    "runtime-smoke-log-policy.json",
    "sbom-attestation-bundle.json",
    "sbom-policy.json",
    "slsa-bundles.jsonl",
    "slsa-verify.json",
    "toolchain.json",
    "trivy-attestation-bundle.json",
    "trivy-version.json",
    "trivy.json",
]
POLARIS_PROMOTION_EVIDENCE_REQUIRED = [
    "promotion-cosign-verify.json",
    "promotion-slsa-verify.json",
    "publication.json",
    "trusted-tag-manifest.json",
]
POLARIS_IMAGE_EVIDENCE_REQUIRED = frozenset(
    POLARIS_CANDIDATE_EVIDENCE_REQUIRED
    + POLARIS_PROMOTION_EVIDENCE_REQUIRED
)
POLARIS_OVERLAY_PREIMAGES = {
    "runtime/server/build.gradle.kts": (
        "6394f787e0b0a48a7a916824306c3e4fe54556c7f639aeb47330a63111e05f16"
    ),
    "runtime/service/build.gradle.kts": (
        "c5f351b2444d37efcbd99b4ad850456b33894257390ccf5ece595002c33f548b"
    ),
}
POLARIS_OVERLAY_POSTIMAGES = {
    "runtime/server/build.gradle.kts": (
        "597b456dec2ac138d40f78fdf27ad84248d83242465ff25c78d3d8a06f37675b"
    ),
    "runtime/service/build.gradle.kts": (
        "f497e81fdb2d34e4fff2ef1b4f8f97c974afca1080d097e5c40fc149f7a6ec95"
    ),
}
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
POSTGRES_PUBLISHER_IDENTITY = (
    "https://github.com/chainguard-images/images/"
    ".github/workflows/release.yaml@refs/heads/main"
)
POSTGRES_PUBLISHER_ISSUER = "https://token.actions.githubusercontent.com"
POSTGRES_PUBLISHER_WORKFLOW_NAME = ".github/workflows/release.yaml"
POSTGRES_PUBLISHER_REPOSITORY = "chainguard-images/images"
POSTGRES_PUBLISHER_REF = "refs/heads/main"
POSTGRES_PUBLISHER_TRIGGER = "push"
POSTGRES_RELEASE_WORKFLOW_SHA = "704e38b436bc40bc9a9d669c05f0d6694bec298b"
POSTGRES_SLSA_WORKFLOW_SHA = "1d360e5f7f3b749f0b1e55b3f75d3eb8db4e7004"
POSTGRES_SLSA_PREDICATE = "https://slsa.dev/provenance/v1"
POSTGRES_SPDX_PREDICATE = "https://spdx.dev/Document"
POSTGRES_SLSA_BUILDER = (
    "https://github.com/chainguard-dev/terraform-provider-apko"
)
POSTGRES_SLSA_BUILD_TYPE = "https://apko.dev/slsa-build-type@v1"
POSTGRES_BUNDLE_MEDIA_TYPE = (
    "application/vnd.dev.sigstore.bundle+json;version=0.3"
)
POSTGRES_TRUSTED_ROOT_MEDIA_TYPE = (
    "application/vnd.dev.sigstore.trustedroot+json;version=0.1"
)
POSTGRES_EVIDENCE_MANIFEST_SHA256 = (
    "c84a126acc195e8349d21a0c1d33f20c4b78e8550082b6d579fd384ad2b6c1a8"
)
POSTGRES_EVIDENCE_MANIFEST_SIZE = 1_675
POSTGRES_EVIDENCE_REQUIRED = frozenset(
    {
        "arm64-manifest.json",
        "arm64-signature-payload.json",
        "arm64-signature.sigstore.json",
        "attestation-manifest.json",
        "cryptographic-verification.json",
        "index-manifest.json",
        "index-signature-payload.json",
        "index-signature.sigstore.json",
        "postgresql-18.4-arm64.cdx.json",
        "slsa-attestation-envelope.json",
        "slsa-provenance.sigstore.json",
        "spdx-attestation-envelope.json",
        "spdx-sbom.sigstore.json",
        "trivy-sbom-version.json",
        "trivy-sbom.json",
        "trivy-version.json",
        "trivy.json",
        "trusted-root.json",
    }
)
POSTGRES_ATTESTATION_LAYERS = {
    "slsa": {
        "predicate_type": POSTGRES_SLSA_PREDICATE,
        "digest": (
            "sha256:"
            "e2fbc51efecdec309574b931d17d1b0d1bc75eabd2df1e0c7c7e2d37c826e12a"
        ),
        "size": 2_964,
        "envelope": "slsa-attestation-envelope.json",
        "bundle": "slsa-provenance.sigstore.json",
        "workflow_sha": POSTGRES_SLSA_WORKFLOW_SHA,
        "log_index": 2_177_108_005,
        "integrated_time": 1_784_157_617,
    },
    "spdx": {
        "predicate_type": POSTGRES_SPDX_PREDICATE,
        "digest": (
            "sha256:"
            "1030922e6384023135ea10cf4a58237c3eb1a32af14ee0db1719b355873fcbb4"
        ),
        "size": 295_772,
        "envelope": "spdx-attestation-envelope.json",
        "bundle": "spdx-sbom.sigstore.json",
        "workflow_sha": POSTGRES_RELEASE_WORKFLOW_SHA,
        "log_index": 2_181_460_833,
        "integrated_time": 1_784_189_281,
    },
}
POSTGRES_SIGNATURES = {
    "index": {
        "reference": POSTGRES_INDEX,
        "digest": POSTGRES_INDEX.removeprefix(
            "cgr.dev/chainguard/postgres@"
        ),
        "payload": "index-signature-payload.json",
        "bundle": "index-signature.sigstore.json",
        "log_index": 2_181_460_214,
        "integrated_time": 1_784_189_273,
    },
    "arm64": {
        "reference": POSTGRES_ARM64,
        "digest": POSTGRES_ARM64.removeprefix(
            "cgr.dev/chainguard/postgres@"
        ),
        "payload": "arm64-signature-payload.json",
        "bundle": "arm64-signature.sigstore.json",
        "log_index": 2_181_460_469,
        "integrated_time": 1_784_189_276,
    },
}

FORBIDDEN_PENDING_PATHS = (
    Path("bootstrap/polaris/v1.6.0/gradle-dependency-inputs.json"),
)
POLARIS_DEPENDENCY_WORKFLOW = Path(
    ".github/workflows/polaris-gradle-dependencies.yml"
)
POLARIS_DEPENDENCY_WORKFLOW_SHA256 = (
    "d6eabefcc9dc9be8225e0d93ba7c25c0b65646fd83957b3a7f8a15f36c7e3528"
)
POLARIS_DEPENDENCY_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:"
    "fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6"
)
POLARIS_DEPENDENCY_MANIFEST_SHA256 = (
    "fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6"
)
POLARIS_DEPENDENCY_ARCHIVE_SHA256 = (
    "18933bfb895c267302f1ee1c80cfb9712eac736ffcefade48dac53f79e8e3bc0"
)
POLARIS_DEPENDENCY_ARCHIVE_SIZE = 701_323_251
POLARIS_DEPENDENCY_PUBLICATION_SHA256 = (
    "1a9ec88b09b49b12ab5131bff739dedfbfa7d2e50fd46448e8169c6452cd3d41"
)
POLARIS_DEPENDENCY_PUBLICATION_SIZE = 2_211
POLARIS_DEPENDENCY_SOURCE_SHA = "4692bab4282dfde2c8d4082e6d706dee9ce79324"
POLARIS_DEPENDENCY_RUN_ID = "29689013375"
POLARIS_DEPENDENCY_RUN_ATTEMPT = "1"
POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY = "TommyKammy/Shirokuma"
POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY_URL = (
    "https://github.com/TommyKammy/Shirokuma"
)
POLARIS_DEPENDENCY_PUBLISHER_REF = "refs/heads/main"
POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA = POLARIS_DEPENDENCY_SOURCE_SHA
POLARIS_DEPENDENCY_PUBLISHER_TRIGGER = "push"
POLARIS_DEPENDENCY_PUBLISHER_IDENTITY = (
    "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
    "polaris-gradle-dependencies.yml@refs/heads/main"
)
POLARIS_DEPENDENCY_PUBLISHER_ISSUER = (
    "https://token.actions.githubusercontent.com"
)
POLARIS_DEPENDENCY_PUBLICATION_ARTIFACT = {
    "id": 8_443_110_667,
    "name": "polaris-gradle-publication-29689013375-1",
    "sha256": (
        "d2618dfdfbce2b645adcab392f6509c05f5b74263f3815f8cce2e2b4b4f89345"
    ),
    "size": 579_565,
    "run_id": POLARIS_DEPENDENCY_RUN_ID,
    "run_attempt": POLARIS_DEPENDENCY_RUN_ATTEMPT,
}
POLARIS_DEPENDENCY_EVIDENCE_RECORDS = {
    "cosign-signature-bundle.json": (
        "36db1df6a0b67e26948a6b87c872f53e6784bfe45438c7d5a2ccb1221b99e753",
        11_127,
    ),
    "cosign-verify.json": (
        "11320bfdaa0d292ba1a8354fc9ca83895df1033e2772b3d21e48faabee0e45db",
        349,
    ),
    "gradle-dependency-inputs.json": (
        "3bab7b055d29be1bc59f2fe605960f49bbceee2639ad68086822c62ee8533841",
        2_172_595,
    ),
    "oci-manifest.json": (
        POLARIS_DEPENDENCY_MANIFEST_SHA256,
        1_077,
    ),
    "offline-build.json": (
        "e1741bbb304c983a0ad7ff1de1c3fb05867eeb3ef836443f3c24b07d4458fe0b",
        593,
    ),
    "publication.json": (
        POLARIS_DEPENDENCY_PUBLICATION_SHA256,
        POLARIS_DEPENDENCY_PUBLICATION_SIZE,
    ),
    "slsa-verify.json": (
        "9caba9a6c0a6359abaf57892819f22cdb57b13fa489faf0b73875235f827472b",
        14_193,
    ),
    "toolchain.json": (
        "45854f6533d0960f94087c132d5d0fdfe6d37443afd42a1f329e24ae852a0319",
        617,
    ),
    "verification-metadata.xml": (
        "b8b1fa91bc9d98eaf676dbab76c5452411fcdf6b11a8c9959c131799c71deaf2",
        879_926,
    ),
}
POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE_RECORDS = {
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
    "evidence.sha256": (
        "026c4d82e9031532323ccb3c31ea83939010982cfcf373644cdcf064e2613409",
        953,
    ),
    "gradle-dependency-inputs.json": (
        "798802722e730174caa581cbffd4f82e5dd4a43aee92201df26f14db4ab005bc",
        2_175_793,
    ),
    "oci-manifest.json": (
        "7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18",
        1_083,
    ),
    "offline-build.json": (
        "12c027f726e62213605fe094a9b4328bcb3351148bdd90a71e5e38c2b766fa68",
        725,
    ),
    "publication.json": (
        "a6453655a183528904bde4e295306ae1cdc92abe67f29479a82ee093975ed9bc",
        3_676,
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
        "171ccaf781d4ae63375b332205d25653ebcd29471e9e9c0cfba1b978144065b8",
        881_256,
    ),
}
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
POLARIS_ADMIN_IMAGE_EVIDENCE_FILES = frozenset(
    {
        "admin-bootstrap-help.json",
        "admin-container-inspect.json",
        "admin-help.json",
        "admin-smoke-log-policy.json",
        "anonymous-image-manifest.json",
        "build-context.sha256",
        "build-input.json",
        "builder-metadata.json",
        "cosign-signature-bundle.json",
        "cosign-verify.json",
        "dependency-input.json",
        "evidence.sha256",
        "image-config.json",
        "image-manifest.json",
        "offline-build.json",
        "polaris-admin-1.6.0-arm64.cdx.json",
        "promotion-cosign-verify.json",
        "promotion-slsa-verify.json",
        "publication.json",
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
        "source-authentication.json",
        "toolchain.json",
        "trivy-attestation-bundle.json",
        "trivy-version.json",
        "trivy.json",
        "trusted-tag-manifest.json",
    }
)
POLARIS_ALLOWED_PATHS = {
    "Containerfile",
    "Containerfile.admin",
    "admin-build-inputs-contract.json",
    "admin-build-inputs-evidence",
    *{
        f"admin-build-inputs-evidence/{filename}"
        for filename in POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE_RECORDS
    },
    "admin-admission.json",
    "admin-image-contract.json",
    "admin-image-evidence",
    *{
        f"admin-image-evidence/{filename}"
        for filename in POLARIS_ADMIN_IMAGE_EVIDENCE_FILES
    },
    "admin-release-evidence.json",
    "admission.json",
    "atomic-admission.json",
    "apache-polaris-release-signing-key.asc",
    "evidence",
    "evidence/README.md",
    *{
        f"evidence/{filename}"
        for filename in POLARIS_DEPENDENCY_EVIDENCE_RECORDS
    },
    "image-evidence",
    "image-evidence/evidence.sha256",
    *{
        f"image-evidence/{filename}"
        for filename in POLARIS_IMAGE_EVIDENCE_REQUIRED
    },
    "release-evidence.json",
    "source.json",
    "patches",
    "patches/0001-shirokuma-bounded-runtime.patch",
    "trusted-build-contract.json",
}
POSTGRES_ALLOWED_PATHS = {
    "admission.json",
    "evidence",
    "evidence/README.md",
    "evidence/evidence.sha256",
    *{
        f"evidence/{filename}"
        for filename in POSTGRES_EVIDENCE_REQUIRED
    },
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
REVIEW_PENDING_WORKFLOW_INVENTORY = {
    ".github/workflows/ci.yml": (
        "9f5684fab946b3b20337fcc6bec1d6ff3f7f432a6a3275a3fa46d2a3a4676244"
    ),
    ".github/workflows/seaweedfs-arm64.yml": (
        "f097273d79c9595d42be816152ff1aabc862faf2667cb0648434280ce8b8ac06"
    ),
    ".github/workflows/security.yml": (
        "3f8c098cf514bf2f38f51ab35c7d5db4437a9b59f73780e8f85221b654f3f5f0"
    ),
    ".github/workflows/trino-maven-dependencies.yml": (
        "7dfbf9201bad42a335199f025756a1e73dd60ea1140ca0664fd3c48a00ad61d1"
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
    "scripts/package_trino_maven_dependencies.py": (
        "d3ed68416f0ccf5aa3af93ac7bdb661049e2a6b5aea790c6f4d0ef3985fd8e72"
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
    "scripts/polaris_runtime_acceptance.py": (
        "0ace333bf74672a9bee05d518338bed4bb8af51d2227dea0bed6b15fae6b0a8c"
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
    "scripts/verify_polaris_admin_build_inputs.py": (
        POLARIS_ADMIN_BUILD_INPUTS_VERIFIER_SHA256
    ),
    "scripts/verify_polaris_admin_image.py": (
        POLARIS_ADMIN_IMAGE_VERIFIER_SHA256
    ),
    "scripts/verify_polaris_runtime.py": (
        "cd87946305f97d13ed909807605fb286e0390f6ab82172864158c58a216adeb7"
    ),
    "scripts/verify_repository_skeleton.py": (
        "b6bbbd383c74b190872bdcf144ede8126d8da5dbeb03e291027aaf276c62c955"
    ),
    "scripts/verify_supply_chain.py": (
        "f5923551dee296c25b7786634340fece2ce89f03d9fd4398d3c182e610a6dedf"
    ),
    "scripts/verify_trivyignore.py": (
        "75cee002d5749c0ec91629edb905c27362bee5c0813b0cbefcb59f161734f445"
    ),
    "scripts/verify_trino_dependency_publisher.py": (
        "6e29bdcb6afd547bdeb94c5465593648f410faf4c7470705f56349d4a171c311"
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
    {"id": "POLARIS-DEP-SNAPSHOT-PUBLICATION", "state": "satisfied"},
    {"id": "POLARIS-DEP-SNAPSHOT-REVIEW", "state": "satisfied"},
    {"id": "POLARIS-IMAGE-MAIN-PUBLICATION", "state": "satisfied"},
    {"id": "POLARIS-IMAGE-EVIDENCE-REVIEW", "state": "satisfied"},
    {"id": "POLARIS-POSTGRES-ATOMIC-ADMISSION", "state": "satisfied"},
]
POLARIS_SERVER_TASKS = [
    ":polaris-server:assemble",
    ":polaris-server:quarkusAppPartsBuild",
]
RUNTIME_ROOTS = (Path("deploy"), Path("charts"), Path("opentofu"))
RUNTIME_GENERATED_DIRS = {".terraform"}
RETAINED_EVIDENCE_ROOT = Path("security/evidence")
POLARIS_RUNTIME_ACTIVATION_CONTRACT = Path(
    "security/polaris-runtime-activation.json"
)
POLARIS_RUNTIME_ACCEPTANCE_RECEIPT = Path(
    "security/evidence/polaris-runtime-acceptance.json"
)
ICEBERG_RUNTIME_ACCEPTANCE_RECEIPT = Path(
    "security/evidence/iceberg-table-bootstrap-runtime-acceptance.json"
)
RETAINED_EVIDENCE_JSON_SUFFIXES = {".json", ".jsonl"}
RETAINED_EVIDENCE_DOCUMENT_SUFFIXES = {".md"}
MAX_DSSE_PAYLOAD_BYTES = 16 * 1024 * 1024
_VERIFIED_DEPENDENCY_CRYPTOGRAPHIC_BINDINGS: set[
    tuple[str, ...]
] = set()
_VALIDATED_DEPENDENCY_DESCRIPTOR_BINDINGS: set[tuple[str, str]] = set()
_POLARIS_DEPENDENCY_PACKAGER_MODULE: Any | None = None
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


def _load_json_value(root: Path, relative: Path, code: str) -> Any:
    path = root / relative
    _expect(
        _is_regular_file_without_symlink_components(root, relative),
        code,
        f"evidence must be a real regular file: {relative}",
    )
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail(code, f"cannot read {relative}: {error}")


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _json_equal_type_sensitive(actual: Any, wanted: Any) -> bool:
    if isinstance(wanted, bool):
        return actual is wanted
    if type(actual) is not type(wanted):
        return False
    if isinstance(wanted, dict):
        return set(actual) == set(wanted) and all(
            _json_equal_type_sensitive(actual[key], value)
            for key, value in wanted.items()
        )
    if isinstance(wanted, list):
        return len(actual) == len(wanted) and all(
            _json_equal_type_sensitive(actual_item, wanted_item)
            for actual_item, wanted_item in zip(actual, wanted)
        )
    return actual == wanted


def _expect_fields(
    value: Mapping[str, Any],
    expected: Mapping[tuple[str, ...], Any],
    code: str,
) -> None:
    for path, wanted in expected.items():
        actual = _nested(value, *path)
        _expect(
            _json_equal_type_sensitive(actual, wanted),
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


def _sha256_and_size(
    root: Path,
    relative: Path,
    code: str,
) -> tuple[str, int]:
    _expect(
        _is_regular_file_without_symlink_components(root, relative),
        code,
        f"evidence must be a real regular file: {relative}",
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with (root / relative).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
    except OSError as error:
        _fail(code, f"cannot read {relative}: {error}")
    return digest.hexdigest(), size


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


def _audit_image_publication_files(root: Path) -> None:
    expected_hashes = {
        POLARIS_CONTAINERFILE: POLARIS_CONTAINERFILE_SHA256,
        POLARIS_SOURCE_OVERLAY: POLARIS_SOURCE_OVERLAY_SHA256,
    }
    for relative, expected_sha256 in expected_hashes.items():
        _expect(
            _is_regular_file_without_symlink_components(root, relative)
            and _sha256(root / relative) == expected_sha256,
            "PUBLICATION_POLICY",
            f"{relative} differs from the reviewed publication policy",
        )

    _expect(
        not (root / POLARIS_IMAGE_WORKFLOW).exists(),
        "PUBLICATION_POLICY",
        "the one-shot Polaris image publisher must remain retired",
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
                "atomic_admission_receipt",
                "source",
                "dependency_snapshot",
                "image_publication",
                "transparency_log",
                "toolchain",
                "evidence",
                "runtime",
            },
            ("lifecycle",): {"state", "next_state"},
            ("atomic_admission_receipt",): {"path", "sha256"},
            ("dependency_snapshot",): {
                "state",
                "admitted",
                "review_checkpoint",
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
                "publication",
                "visibility_bootstrap",
                "tools",
                "offline_proof",
            },
            ("dependency_snapshot", "review_checkpoint"): {
                "merge_commit",
                "reviewed_contract_sha256",
                "reviewed_admission_sha256",
            },
            ("dependency_snapshot", "descriptor"): {
                "path",
                "sha256",
                "size",
            },
            ("dependency_snapshot", "verification_metadata"): {
                "path",
                "sha256",
                "size",
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
            ("dependency_snapshot", "publication"): {
                "record",
                "actions_artifact",
                "publisher",
            },
            ("dependency_snapshot", "publication", "record"): {
                "path",
                "sha256",
                "size",
            },
            ("dependency_snapshot", "publication", "actions_artifact"): {
                "id",
                "name",
                "sha256",
                "size",
                "run_id",
                "run_attempt",
            },
            ("dependency_snapshot", "publication", "publisher"): {
                "path",
                "sha256",
                "repository",
                "ref",
                "source_sha",
                "workflow_sha",
                "event",
                "retired",
            },
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
                "reference",
                "digest",
                "release_evidence",
                "containerfile",
                "source_overlay",
                "runtime_base",
                "vulnerability_gate",
                "publication_boundary",
                "workflow",
            },
            ("image_publication", "release_evidence"): {
                "path",
                "sha256",
            },
            ("image_publication", "containerfile"): {"path", "sha256"},
            ("image_publication", "source_overlay"): {
                "path",
                "sha256",
                "state",
                "applied_after_source_verification",
                "preimages",
                "postimages",
                "excluded_capabilities",
                "retained_capabilities",
                "forbidden_runtime_jar_markers",
            },
            ("image_publication", "source_overlay", "preimages"): set(
                POLARIS_OVERLAY_PREIMAGES
            ),
            ("image_publication", "source_overlay", "postimages"): set(
                POLARIS_OVERLAY_POSTIMAGES
            ),
            ("image_publication", "runtime_base"): {
                "index",
                "arm64_manifest",
                "distribution",
                "java_version",
                "user",
            },
            ("image_publication", "vulnerability_gate"): {
                "scanner",
                "version",
                "scanners",
                "severity",
                "ignore_unfixed",
                "vulnerability_types",
                "maximum_high",
                "maximum_critical",
            },
            ("image_publication", "publication_boundary"): {
                "repository",
                "ref",
                "workflow_sha_equals_source_sha",
                "quarantine_before_promotion",
                "anonymous_exact_digest_verification",
                "credential_fallback_permitted",
                "release_evidence_committed",
                "admission_permitted",
            },
            ("image_publication", "workflow"): {
                "path",
                "sha256",
                "repository",
                "ref",
                "event",
                "source_sha",
                "workflow_sha",
                "run_id",
                "run_attempt",
                "retired",
            },
            ("transparency_log",): {
                "base_url",
                "major_api_version",
                "entry_lookup_path",
            },
            ("toolchain",): {"cosign"},
            ("toolchain", "cosign"): {
                "version",
                "bundle_media_type",
                "predicate_type",
                "registry_download_format",
                "legacy_signature_records_permitted",
                "detached_bundle_role",
                "authoritative_image_verification",
                "attestation_predicates",
            },
            ("toolchain", "cosign", "attestation_predicates"): {
                "sbom",
                "vulnerability_scan",
            },
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "sbom",
            ): {
                "artifact",
                "cli_type",
                "predicate_type",
            },
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "vulnerability_scan",
            ): {
                "artifact",
                "cli_type",
                "predicate_type",
            },
            ("evidence",): {
                "directory",
                "candidate_required",
                "promotion_required",
                "self_manifest",
                "actions_artifact_role",
                "candidate_retention_days",
                "final_retention_days",
            },
            ("evidence", "self_manifest"): {
                "path",
                "sha256",
                "entries",
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
            ("schema_version",): 7,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("lifecycle", "state"): "runtime_acceptance_pending",
            ("lifecycle", "next_state"): "runtime_accepted",
            (
                "atomic_admission_receipt",
                "path",
            ): POLARIS_ATOMIC_ADMISSION.as_posix(),
            (
                "atomic_admission_receipt",
                "sha256",
            ): POLARIS_ATOMIC_ADMISSION_SHA256,
            ("source", "record"): POLARIS_SOURCE.as_posix(),
            ("source", "record_sha256"): POLARIS_SOURCE_SHA256,
            ("source", "archive_sha512"): POLARIS_ARCHIVE_SHA512,
            ("source", "git_commit"): POLARIS_COMMIT,
            ("source", "git_tree"): POLARIS_TREE,
            ("source", "builder_index"): BUILDER_INDEX,
            ("source", "builder_arm64_manifest"): BUILDER_ARM64,
            ("source", "java_major"): 21,
            ("source", "gradle_version"): "9.6.0",
            ("dependency_snapshot", "state"): "approved_for_image_build",
            ("dependency_snapshot", "admitted"): False,
            (
                "dependency_snapshot",
                "review_checkpoint",
                "merge_commit",
            ): POLARIS_DEPENDENCY_REVIEW_MERGE,
            (
                "dependency_snapshot",
                "review_checkpoint",
                "reviewed_contract_sha256",
            ): REVIEWED_POLARIS_CONTRACT_SHA256,
            (
                "dependency_snapshot",
                "review_checkpoint",
                "reviewed_admission_sha256",
            ): REVIEWED_POLARIS_ADMISSION_SHA256,
            (
                "dependency_snapshot",
                "artifact_repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies",
            (
                "dependency_snapshot",
                "artifact_reference",
            ): POLARIS_DEPENDENCY_REFERENCE,
            (
                "dependency_snapshot",
                "artifact_type",
            ): "application/vnd.shirokuma.polaris.gradle-dependencies.v1",
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
            (
                "dependency_snapshot",
                "archive",
                "format",
            ): "pax+gzip",
            (
                "dependency_snapshot",
                "archive",
                "media_type",
            ): "application/vnd.shirokuma.gradle-cache.v1.tar+gzip",
            (
                "dependency_snapshot",
                "archive",
                "mtime",
            ): 0,
            (
                "dependency_snapshot",
                "archive",
                "uid",
            ): 0,
            (
                "dependency_snapshot",
                "archive",
                "gid",
            ): 0,
            (
                "dependency_snapshot",
                "archive",
                "file_mode",
            ): "0644",
            (
                "dependency_snapshot",
                "archive",
                "directory_mode",
            ): "0755",
            (
                "dependency_snapshot",
                "descriptor_media_type",
            ): (
                "application/vnd.shirokuma."
                "gradle-dependency-descriptor.v1+json"
            ),
            (
                "dependency_snapshot",
                "verification_metadata_media_type",
            ): "application/vnd.gradle.dependency-verification.v1+xml",
            (
                "dependency_snapshot",
                "descriptor",
                "path",
            ): (
                "bootstrap/polaris/v1.6.0/evidence/"
                "gradle-dependency-inputs.json"
            ),
            (
                "dependency_snapshot",
                "descriptor",
                "sha256",
            ): POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "gradle-dependency-inputs.json"
            ][0],
            (
                "dependency_snapshot",
                "descriptor",
                "size",
            ): POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "gradle-dependency-inputs.json"
            ][1],
            (
                "dependency_snapshot",
                "verification_metadata",
                "path",
            ): (
                "bootstrap/polaris/v1.6.0/evidence/"
                "verification-metadata.xml"
            ),
            (
                "dependency_snapshot",
                "verification_metadata",
                "sha256",
            ): POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "verification-metadata.xml"
            ][0],
            (
                "dependency_snapshot",
                "verification_metadata",
                "size",
            ): POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "verification-metadata.xml"
            ][1],
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
                "publication",
                "record",
                "path",
            ): "bootstrap/polaris/v1.6.0/evidence/publication.json",
            (
                "dependency_snapshot",
                "publication",
                "record",
                "sha256",
            ): POLARIS_DEPENDENCY_PUBLICATION_SHA256,
            (
                "dependency_snapshot",
                "publication",
                "record",
                "size",
            ): POLARIS_DEPENDENCY_PUBLICATION_SIZE,
            (
                "dependency_snapshot",
                "publication",
                "actions_artifact",
            ): POLARIS_DEPENDENCY_PUBLICATION_ARTIFACT,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "path",
            ): POLARIS_DEPENDENCY_WORKFLOW.as_posix(),
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "sha256",
            ): POLARIS_DEPENDENCY_WORKFLOW_SHA256,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "repository",
            ): POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "ref",
            ): POLARIS_DEPENDENCY_PUBLISHER_REF,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "source_sha",
            ): POLARIS_DEPENDENCY_SOURCE_SHA,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "workflow_sha",
            ): POLARIS_DEPENDENCY_SOURCE_SHA,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "event",
            ): POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
            (
                "dependency_snapshot",
                "publication",
                "publisher",
                "retired",
            ): True,
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
            ): (
                "ac7156f93a21e903f7ad606c792f3560f17e0cd0e36365634701b1e7cc4e4eca"
            ),
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
            ): POLARIS_DEPENDENCY_PUBLISHER_ISSUER,
            (
                "dependency_snapshot",
                "tools",
                "cosign",
                "identity",
            ): POLARIS_DEPENDENCY_PUBLISHER_IDENTITY,
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
            ("image_publication", "state"): "admitted",
            ("image_publication", "enabled"): False,
            (
                "image_publication",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris",
            ("image_publication", "trusted_tag"): "1.6.0-arm64",
            ("image_publication", "reference"): POLARIS_IMAGE_REFERENCE,
            ("image_publication", "digest"): POLARIS_IMAGE_DIGEST,
            (
                "image_publication",
                "release_evidence",
                "path",
            ): POLARIS_RELEASE_EVIDENCE.as_posix(),
            (
                "image_publication",
                "release_evidence",
                "sha256",
            ): POLARIS_RELEASE_EVIDENCE_SHA256,
            (
                "image_publication",
                "containerfile",
                "path",
            ): POLARIS_CONTAINERFILE.as_posix(),
            (
                "image_publication",
                "containerfile",
                "sha256",
            ): POLARIS_CONTAINERFILE_SHA256,
            (
                "image_publication",
                "source_overlay",
                "path",
            ): POLARIS_SOURCE_OVERLAY.as_posix(),
            (
                "image_publication",
                "source_overlay",
                "sha256",
            ): POLARIS_SOURCE_OVERLAY_SHA256,
            (
                "image_publication",
                "source_overlay",
                "state",
            ): "approved_bounded_runtime",
            (
                "image_publication",
                "source_overlay",
                "applied_after_source_verification",
            ): True,
            (
                "image_publication",
                "source_overlay",
                "preimages",
            ): POLARIS_OVERLAY_PREIMAGES,
            (
                "image_publication",
                "source_overlay",
                "postimages",
            ): POLARIS_OVERLAY_POSTIMAGES,
            (
                "image_publication",
                "source_overlay",
                "excluded_capabilities",
            ): [
                "hadoop-file-io",
                "hadoop-federation",
                "ranger-authorization",
            ],
            (
                "image_publication",
                "source_overlay",
                "retained_capabilities",
            ): [
                "native-polaris-catalog",
                "opa-authorization",
                "postgresql-persistence",
                "s3-storage",
            ],
            (
                "image_publication",
                "source_overlay",
                "forbidden_runtime_jar_markers",
            ): ["hadoop", "ranger", "jetty-http"],
            (
                "image_publication",
                "runtime_base",
                "index",
            ): IMAGE_RUNTIME_INDEX,
            (
                "image_publication",
                "runtime_base",
                "arm64_manifest",
            ): IMAGE_RUNTIME_ARM64,
            (
                "image_publication",
                "runtime_base",
                "distribution",
            ): "Amazon Corretto",
            (
                "image_publication",
                "runtime_base",
                "java_version",
            ): "21.0.11",
            (
                "image_publication",
                "runtime_base",
                "user",
            ): "10000:10001",
            (
                "image_publication",
                "vulnerability_gate",
                "scanner",
            ): "trivy",
            (
                "image_publication",
                "vulnerability_gate",
                "version",
            ): "0.72.0",
            (
                "image_publication",
                "vulnerability_gate",
                "scanners",
            ): "vuln",
            (
                "image_publication",
                "vulnerability_gate",
                "severity",
            ): "HIGH,CRITICAL",
            (
                "image_publication",
                "vulnerability_gate",
                "ignore_unfixed",
            ): False,
            (
                "image_publication",
                "vulnerability_gate",
                "vulnerability_types",
            ): "os,library",
            (
                "image_publication",
                "vulnerability_gate",
                "maximum_high",
            ): 0,
            (
                "image_publication",
                "vulnerability_gate",
                "maximum_critical",
            ): 0,
            (
                "image_publication",
                "publication_boundary",
                "repository",
            ): "TommyKammy/Shirokuma",
            (
                "image_publication",
                "publication_boundary",
                "ref",
            ): "refs/heads/main",
            (
                "image_publication",
                "publication_boundary",
                "workflow_sha_equals_source_sha",
            ): True,
            (
                "image_publication",
                "publication_boundary",
                "quarantine_before_promotion",
            ): True,
            (
                "image_publication",
                "publication_boundary",
                "anonymous_exact_digest_verification",
            ): True,
            (
                "image_publication",
                "publication_boundary",
                "credential_fallback_permitted",
            ): False,
            (
                "image_publication",
                "publication_boundary",
                "release_evidence_committed",
            ): True,
            (
                "image_publication",
                "publication_boundary",
                "admission_permitted",
            ): True,
            (
                "image_publication",
                "workflow",
                "path",
            ): POLARIS_IMAGE_WORKFLOW.as_posix(),
            (
                "image_publication",
                "workflow",
                "sha256",
            ): POLARIS_IMAGE_WORKFLOW_SHA256,
            (
                "image_publication",
                "workflow",
                "repository",
            ): POLARIS_IMAGE_PUBLISHER_REPOSITORY,
            (
                "image_publication",
                "workflow",
                "ref",
            ): POLARIS_IMAGE_PUBLISHER_REF,
            (
                "image_publication",
                "workflow",
                "event",
            ): POLARIS_IMAGE_PUBLISHER_TRIGGER,
            (
                "image_publication",
                "workflow",
                "source_sha",
            ): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            (
                "image_publication",
                "workflow",
                "workflow_sha",
            ): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            (
                "image_publication",
                "workflow",
                "run_id",
            ): POLARIS_IMAGE_PUBLISHER_RUN_ID,
            (
                "image_publication",
                "workflow",
                "run_attempt",
            ): POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT,
            (
                "image_publication",
                "workflow",
                "retired",
            ): True,
            (
                "transparency_log",
                "base_url",
            ): "https://rekor.sigstore.dev",
            ("transparency_log", "major_api_version"): 1,
            (
                "transparency_log",
                "entry_lookup_path",
            ): "/api/v1/log/entries?logIndex={log_index}",
            ("toolchain", "cosign", "version"): "v3.1.1",
            (
                "toolchain",
                "cosign",
                "bundle_media_type",
            ): "application/vnd.dev.sigstore.bundle.v0.3+json",
            (
                "toolchain",
                "cosign",
                "predicate_type",
            ): "https://sigstore.dev/cosign/sign/v1",
            (
                "toolchain",
                "cosign",
                "registry_download_format",
            ): "sigstore-bundle-v0.3-jsonl",
            (
                "toolchain",
                "cosign",
                "legacy_signature_records_permitted",
            ): False,
            (
                "toolchain",
                "cosign",
                "detached_bundle_role",
            ): "bind-image-digest-to-raw-oci-manifest",
            (
                "toolchain",
                "cosign",
                "authoritative_image_verification",
            ): "cosign verify IMAGE@DIGEST",
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "sbom",
                "artifact",
            ): "sbom-attestation-bundle.json",
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "sbom",
                "cli_type",
            ): "cyclonedx",
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "sbom",
                "predicate_type",
            ): "https://cyclonedx.org/bom",
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "vulnerability_scan",
                "artifact",
            ): "trivy-attestation-bundle.json",
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "vulnerability_scan",
                "cli_type",
            ): "https://shirokuma.dev/attestations/trivy/v1",
            (
                "toolchain",
                "cosign",
                "attestation_predicates",
                "vulnerability_scan",
                "predicate_type",
            ): "https://shirokuma.dev/attestations/trivy/v1",
            (
                "evidence",
                "directory",
            ): POLARIS_IMAGE_EVIDENCE.as_posix(),
            (
                "evidence",
                "candidate_required",
            ): POLARIS_CANDIDATE_EVIDENCE_REQUIRED,
            (
                "evidence",
                "promotion_required",
            ): POLARIS_PROMOTION_EVIDENCE_REQUIRED,
            (
                "evidence",
                "actions_artifact_role",
            ): "retained mirror only",
            (
                "evidence",
                "self_manifest",
                "path",
            ): (POLARIS_IMAGE_EVIDENCE / "evidence.sha256").as_posix(),
            (
                "evidence",
                "self_manifest",
                "sha256",
            ): POLARIS_IMAGE_EVIDENCE_MANIFEST_SHA256,
            ("evidence", "self_manifest", "entries"): 32,
            ("evidence", "candidate_retention_days"): 30,
            ("evidence", "final_retention_days"): 30,
            ("runtime", "state"): "blocked_runtime_acceptance",
            ("runtime", "enabled"): False,
            ("runtime", "admission_record"): POLARIS_ADMISSION.as_posix(),
            ("runtime", "atomic_peer"): POSTGRES_ADMISSION.as_posix(),
        },
        "CONTRACT_STATE",
    )
    _expect(
        _nested(contract, "source", "tasks") == POLARIS_SERVER_TASKS
        and _nested(
            contract,
            "dependency_snapshot",
            "offline_proof",
            "tasks",
        )
        == POLARIS_SERVER_TASKS,
        "CONTRACT_STATE",
        "source and offline task closures changed",
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
        not (root / POLARIS_DEPENDENCY_WORKFLOW).exists(),
        "CONTRACT_STATE",
        "the one-shot dependency publisher must be retired",
    )
    _audit_image_publication_files(root)
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
                "atomic_admission_receipt",
                "image_publication",
                "resident_ledger",
                "runtime_manifests",
                "blocking_controls",
                "next_action",
            },
            ("dependency_snapshot",): {
                "state",
                "admitted",
                "repository",
                "reference",
                "publication_evidence",
                "review_checkpoint",
            },
            ("dependency_snapshot", "publication_evidence"): {
                "path",
                "sha256",
            },
            ("dependency_snapshot", "review_checkpoint"): {
                "merge_commit",
                "reviewed_contract_sha256",
                "reviewed_admission_sha256",
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
            ("planned_candidate", "release_evidence"): {
                "path",
                "sha256",
            },
            ("atomic_admission_receipt",): {"path", "sha256"},
            ("image_publication",): {
                "state",
                "enabled",
                "admitted",
                "reference",
                "digest",
                "containerfile",
                "release_evidence",
                "workflow",
            },
            ("image_publication", "release_evidence"): {
                "path",
                "sha256",
            },
            ("image_publication", "workflow"): {
                "path",
                "sha256",
                "source_sha",
                "run_id",
                "run_attempt",
                "retired",
            },
            ("resident_ledger",): {"permitted", "atomic_with"},
            ("runtime_manifests",): {"permitted", "forbidden_roots"},
        },
        "POLARIS_ADMISSION",
    )
    _expect_fields(
        admission,
        {
            ("schema_version",): 6,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("admission",): "approved",
            ("state",): "runtime_acceptance_pending",
            ("source_record",): POLARIS_SOURCE.as_posix(),
            ("source_record_sha256",): POLARIS_SOURCE_SHA256,
            ("build_contract",): POLARIS_CONTRACT.as_posix(),
            ("build_contract_sha256",): POLARIS_CONTRACT_SHA256,
            (
                "dependency_snapshot",
                "state",
            ): "approved_for_image_build",
            (
                "dependency_snapshot",
                "admitted",
            ): False,
            (
                "dependency_snapshot",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies",
            (
                "dependency_snapshot",
                "reference",
            ): POLARIS_DEPENDENCY_REFERENCE,
            (
                "dependency_snapshot",
                "publication_evidence",
                "path",
            ): "bootstrap/polaris/v1.6.0/evidence/publication.json",
            (
                "dependency_snapshot",
                "publication_evidence",
                "sha256",
            ): POLARIS_DEPENDENCY_PUBLICATION_SHA256,
            (
                "dependency_snapshot",
                "review_checkpoint",
                "merge_commit",
            ): POLARIS_DEPENDENCY_REVIEW_MERGE,
            (
                "dependency_snapshot",
                "review_checkpoint",
                "reviewed_contract_sha256",
            ): REVIEWED_POLARIS_CONTRACT_SHA256,
            (
                "dependency_snapshot",
                "review_checkpoint",
                "reviewed_admission_sha256",
            ): REVIEWED_POLARIS_ADMISSION_SHA256,
            ("upstream_image_assessment", "admission"): "rejected",
            (
                "upstream_image_assessment",
                "reference",
            ): "apache/polaris@sha256:9738b2052dea20aabf0cd42521424ff963fee41b0ee888fef9f512efb256602a",
            (
                "planned_candidate",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris",
            ("planned_candidate", "reference"): POLARIS_IMAGE_REFERENCE,
            (
                "planned_candidate",
                "release_evidence",
                "path",
            ): POLARIS_RELEASE_EVIDENCE.as_posix(),
            (
                "planned_candidate",
                "release_evidence",
                "sha256",
            ): POLARIS_RELEASE_EVIDENCE_SHA256,
            (
                "atomic_admission_receipt",
                "path",
            ): POLARIS_ATOMIC_ADMISSION.as_posix(),
            (
                "atomic_admission_receipt",
                "sha256",
            ): POLARIS_ATOMIC_ADMISSION_SHA256,
            (
                "image_publication",
                "state",
            ): "admitted",
            ("image_publication", "enabled"): False,
            ("image_publication", "admitted"): True,
            (
                "image_publication",
                "reference",
            ): POLARIS_IMAGE_REFERENCE,
            ("image_publication", "digest"): POLARIS_IMAGE_DIGEST,
            (
                "image_publication",
                "containerfile",
            ): POLARIS_CONTAINERFILE.as_posix(),
            (
                "image_publication",
                "release_evidence",
                "path",
            ): POLARIS_RELEASE_EVIDENCE.as_posix(),
            (
                "image_publication",
                "release_evidence",
                "sha256",
            ): POLARIS_RELEASE_EVIDENCE_SHA256,
            (
                "image_publication",
                "workflow",
                "path",
            ): POLARIS_IMAGE_WORKFLOW.as_posix(),
            (
                "image_publication",
                "workflow",
                "sha256",
            ): POLARIS_IMAGE_WORKFLOW_SHA256,
            (
                "image_publication",
                "workflow",
                "source_sha",
            ): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            (
                "image_publication",
                "workflow",
                "run_id",
            ): POLARIS_IMAGE_PUBLISHER_RUN_ID,
            (
                "image_publication",
                "workflow",
                "run_attempt",
            ): POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT,
            (
                "image_publication",
                "workflow",
                "retired",
            ): True,
            ("resident_ledger", "permitted"): True,
            ("resident_ledger", "atomic_with"): "postgresql",
            ("runtime_manifests", "permitted"): False,
            (
                "runtime_manifests",
                "forbidden_roots",
            ): ["deploy", "charts", "opentofu"],
            (
                "next_action",
            ): "complete-runtime-acceptance",
        },
        "POLARIS_ADMISSION",
    )
    _expect(
        admission.get("blocking_controls") == POLARIS_BLOCKING_CONTROLS,
        "POLARIS_ADMISSION",
        "all five blocking control states must remain explicit",
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


def _audit_dependency_evidence_file(
    root: Path,
    filename: str,
) -> None:
    expected_sha256, expected_size = POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
        filename
    ]
    relative = POLARIS_EVIDENCE / filename
    actual_sha256, actual_size = _sha256_and_size(
        root,
        relative,
        "DEPENDENCY_EVIDENCE",
    )
    _expect(
        (actual_sha256, actual_size) == (expected_sha256, expected_size),
        "DEPENDENCY_EVIDENCE",
        f"{relative} differs from the retained publication evidence",
    )


def _audit_admin_build_inputs_retained_evidence(root: Path) -> None:
    contract_sha256, _ = _sha256_and_size(
        root,
        POLARIS_ADMIN_BUILD_INPUTS_CONTRACT,
        "ADMIN_DEPENDENCY_EVIDENCE",
    )
    _expect(
        contract_sha256 == POLARIS_ADMIN_BUILD_INPUTS_CONTRACT_SHA256,
        "ADMIN_DEPENDENCY_EVIDENCE",
        "admin build-input contract differs from the reviewed evidence state",
    )

    directory = root / POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "ADMIN_DEPENDENCY_EVIDENCE",
        "admin build-input evidence root must be a real directory",
    )
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expected = set(POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE_RECORDS)
    _expect(
        actual == expected,
        "ADMIN_DEPENDENCY_EVIDENCE",
        "admin build-input evidence inventory must be closed; "
        f"expected {sorted(expected)}, found {sorted(actual)}",
    )
    retained_records = POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE_RECORDS
    for filename, retained in retained_records.items():
        relative = POLARIS_ADMIN_BUILD_INPUTS_EVIDENCE / filename
        observed = _sha256_and_size(
            root,
            relative,
            "ADMIN_DEPENDENCY_EVIDENCE",
        )
        _expect(
            observed == retained,
            "ADMIN_DEPENDENCY_EVIDENCE",
            f"{relative} differs from the retained publication evidence",
        )


def _audit_admin_image_publication_policy(root: Path) -> None:
    expected_files = {
        POLARIS_ADMIN_ADMISSION: POLARIS_ADMIN_ADMISSION_SHA256,
        POLARIS_ADMIN_IMAGE_CONTRACT: POLARIS_ADMIN_IMAGE_CONTRACT_SHA256,
        POLARIS_ADMIN_IMAGE_CONTAINERFILE: (
            POLARIS_ADMIN_IMAGE_CONTAINERFILE_SHA256
        ),
        POLARIS_ADMIN_IMAGE_VERIFIER: POLARIS_ADMIN_IMAGE_VERIFIER_SHA256,
        POLARIS_ADMIN_RELEASE_EVIDENCE: POLARIS_ADMIN_RELEASE_EVIDENCE_SHA256,
        POLARIS_ADMIN_IMAGE_EVIDENCE
        / "evidence.sha256": POLARIS_ADMIN_IMAGE_EVIDENCE_MANIFEST_SHA256,
    }
    for relative, expected_sha256 in expected_files.items():
        actual_sha256, _ = _sha256_and_size(
            root,
            relative,
            "ADMIN_IMAGE_POLICY",
        )
        _expect(
            actual_sha256 == expected_sha256,
            "ADMIN_IMAGE_POLICY",
            f"{relative} differs from the reviewed Admin image policy",
        )

    _expect(
        not os.path.lexists(root / ".github/workflows/polaris-admin-arm64.yml"),
        "ADMIN_IMAGE_POLICY",
        "retired Polaris Admin image publisher was restored",
    )

    verifier_path = root / POLARIS_ADMIN_IMAGE_VERIFIER
    try:
        spec = importlib.util.spec_from_file_location(
            "_polaris_admin_image_policy_for_global_audit",
            verifier_path,
        )
        if spec is None or spec.loader is None:
            _fail("ADMIN_IMAGE_POLICY", f"cannot load {verifier_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.audit_publication_bootstrap(root)
    except (OSError, ImportError, AttributeError, RuntimeError) as error:
        _fail("ADMIN_IMAGE_POLICY", f"Admin image policy audit failed: {error}")


def _dependency_packager_module(root: Path) -> Any:
    global _POLARIS_DEPENDENCY_PACKAGER_MODULE
    if _POLARIS_DEPENDENCY_PACKAGER_MODULE is not None:
        return _POLARIS_DEPENDENCY_PACKAGER_MODULE
    path = root / POLARIS_DEPENDENCY_PACKAGER
    try:
        spec = importlib.util.spec_from_file_location(
            "_polaris_dependency_packager_for_evidence",
            path,
        )
        if spec is None or spec.loader is None:
            _fail(
                "DEPENDENCY_EVIDENCE",
                "cannot load the reviewed dependency packager",
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (OSError, ImportError, AttributeError) as error:
        _fail(
            "DEPENDENCY_EVIDENCE",
            f"cannot load the reviewed dependency packager: {error}",
        )
    _POLARIS_DEPENDENCY_PACKAGER_MODULE = module
    return module


def _audit_dependency_descriptor(
    root: Path,
    publication: Mapping[str, Any],
) -> Mapping[str, Any]:
    descriptor_path = POLARIS_EVIDENCE / "gradle-dependency-inputs.json"
    metadata_path = POLARIS_EVIDENCE / "verification-metadata.xml"
    descriptor = _load_json_value(
        root,
        descriptor_path,
        "DEPENDENCY_EVIDENCE",
    )
    _expect(
        isinstance(descriptor, Mapping),
        "DEPENDENCY_EVIDENCE",
        f"{descriptor_path} must be a JSON object",
    )
    descriptor_binding = (
        POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
            "gradle-dependency-inputs.json"
        ][0],
        POLARIS_DEPENDENCY_EVIDENCE_RECORDS["verification-metadata.xml"][0],
    )
    if descriptor_binding not in _VALIDATED_DEPENDENCY_DESCRIPTOR_BINDINGS:
        module = _dependency_packager_module(root)
        validator = getattr(module, "_validate_descriptor", None)
        _expect(
            callable(validator),
            "DEPENDENCY_EVIDENCE",
            "reviewed dependency packager lacks descriptor validation",
        )
        try:
            validator(descriptor, root / metadata_path)
        except Exception as error:
            _fail(
                "DEPENDENCY_EVIDENCE",
                f"dependency descriptor validation failed: {error}",
            )
        _VALIDATED_DEPENDENCY_DESCRIPTOR_BINDINGS.add(descriptor_binding)

    archive = _nested(descriptor, "archive")
    metadata = _nested(descriptor, "verification_metadata")
    _expect(
        isinstance(archive, Mapping) and isinstance(metadata, Mapping),
        "DEPENDENCY_EVIDENCE",
        "dependency descriptor archive and metadata records must be objects",
    )
    _expect(
        dict(archive)
        == {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "media_type": (
                "application/vnd.shirokuma.gradle-cache.v1.tar+gzip"
            ),
            "sha256": POLARIS_DEPENDENCY_ARCHIVE_SHA256,
            "size": POLARIS_DEPENDENCY_ARCHIVE_SIZE,
        },
        "DEPENDENCY_EVIDENCE",
        "dependency descriptor archive binding changed",
    )
    _expect(
        dict(metadata)
        == {
            "filename": "verification-metadata.xml",
            "media_type": (
                "application/vnd.gradle.dependency-verification.v1+xml"
            ),
            "mode": "strict",
            "sha256": POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "verification-metadata.xml"
            ][0],
            "size": POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "verification-metadata.xml"
            ][1],
        },
        "DEPENDENCY_EVIDENCE",
        "dependency descriptor verification-metadata binding changed",
    )
    _expect(
        _nested(publication, "descriptor", "sha256")
        == POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
            "gradle-dependency-inputs.json"
        ][0]
        and _nested(publication, "verification_metadata", "sha256")
        == metadata["sha256"]
        and _nested(publication, "archive", "sha256") == archive["sha256"],
        "DEPENDENCY_EVIDENCE",
        "publication and descriptor dependency bindings differ",
    )
    return descriptor


def _audit_dependency_oci_manifest(
    root: Path,
    publication: Mapping[str, Any],
) -> Mapping[str, Any]:
    relative = POLARIS_EVIDENCE / "oci-manifest.json"
    manifest = _load_json_value(root, relative, "DEPENDENCY_EVIDENCE")
    _expect(
        isinstance(manifest, Mapping),
        "DEPENDENCY_EVIDENCE",
        f"{relative} must be a JSON object",
    )
    _expect_keysets(
        manifest,
        {
            (): {
                "schemaVersion",
                "mediaType",
                "artifactType",
                "config",
                "layers",
                "annotations",
            },
            ("config",): {"mediaType", "digest", "size", "data"},
            ("annotations",): {
                "org.opencontainers.image.created",
                "org.opencontainers.image.revision",
                "org.opencontainers.image.source",
            },
        },
        "DEPENDENCY_EVIDENCE",
    )
    _expect_fields(
        manifest,
        {
            ("schemaVersion",): 2,
            ("mediaType",): "application/vnd.oci.image.manifest.v1+json",
            (
                "artifactType",
            ): "application/vnd.shirokuma.polaris.gradle-dependencies.v1",
            ("config", "mediaType"): "application/vnd.oci.empty.v1+json",
            (
                "config",
                "digest",
            ): "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
            ("config", "size"): 2,
            ("config", "data"): "e30=",
            (
                "annotations",
                "org.opencontainers.image.created",
            ): "2026-07-19T22:30:37+09:00",
            (
                "annotations",
                "org.opencontainers.image.revision",
            ): POLARIS_DEPENDENCY_SOURCE_SHA,
            (
                "annotations",
                "org.opencontainers.image.source",
            ): POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY_URL,
        },
        "DEPENDENCY_EVIDENCE",
    )
    layers = manifest.get("layers")
    _expect(
        layers
        == [
            {
                "mediaType": (
                    "application/vnd.shirokuma."
                    "gradle-dependency-descriptor.v1+json"
                ),
                "digest": (
                    "sha256:"
                    + POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                        "gradle-dependency-inputs.json"
                    ][0]
                ),
                "size": POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                    "gradle-dependency-inputs.json"
                ][1],
                "annotations": {
                    "org.opencontainers.image.title": (
                        "gradle-dependency-inputs.json"
                    )
                },
            },
            {
                "mediaType": (
                    "application/vnd.shirokuma.gradle-cache.v1.tar+gzip"
                ),
                "digest": "sha256:" + POLARIS_DEPENDENCY_ARCHIVE_SHA256,
                "size": POLARIS_DEPENDENCY_ARCHIVE_SIZE,
                "annotations": {
                    "org.opencontainers.image.title": (
                        "polaris-gradle-dependencies-1.6.0.tar.gz"
                    )
                },
            },
        ],
        "DEPENDENCY_EVIDENCE",
        "OCI descriptor/archive layer order or binding changed",
    )
    _expect(
        _nested(publication, "manifest", "sha256")
        == POLARIS_DEPENDENCY_MANIFEST_SHA256
        and publication.get("reference") == POLARIS_DEPENDENCY_REFERENCE,
        "DEPENDENCY_EVIDENCE",
        "publication reference does not bind the raw OCI manifest",
    )
    return manifest


def _run_cosign(
    root: Path,
    arguments: list[str],
    purpose: str,
    *,
    code: str = "DEPENDENCY_EVIDENCE",
    env: Optional[Mapping[str, str]] = None,
) -> None:
    try:
        run_options: dict[str, Any] = {
            "cwd": root,
            "text": True,
            "capture_output": True,
            "check": False,
            "timeout": 60,
        }
        if env is not None:
            run_options["env"] = dict(env)
        result = subprocess.run(
            ["cosign", *arguments],
            **run_options,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _fail(
            code,
            f"cannot run Cosign for {purpose}: {error}",
        )
    _expect(
        result.returncode == 0,
        code,
        f"Cosign {purpose} failed: "
        f"{(result.stderr or result.stdout).strip()[-1000:]}",
    )


DependencyCryptoVerifier = Callable[
    [Path, Path, Path, Mapping[str, Any]],
    None,
]


def _reverify_dependency_sigstore_cryptographically(
    root: Path,
    manifest: Path,
    cosign_bundle: Path,
    nested_bundle: Mapping[str, Any],
) -> None:
    binding = (
        POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
            "cosign-signature-bundle.json"
        ][0],
        POLARIS_DEPENDENCY_EVIDENCE_RECORDS["slsa-verify.json"][0],
        POLARIS_DEPENDENCY_MANIFEST_SHA256,
        POLARIS_DEPENDENCY_PUBLISHER_IDENTITY,
        POLARIS_DEPENDENCY_PUBLISHER_ISSUER,
        POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
        POLARIS_DEPENDENCY_PUBLISHER_REF,
        POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA,
        POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
    )
    if binding in _VERIFIED_DEPENDENCY_CRYPTOGRAPHIC_BINDINGS:
        return
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
        _fail("DEPENDENCY_EVIDENCE", f"cannot inspect Cosign: {error}")
    _expect(
        version.returncode == 0
        and re.search(r"(?m)^GitVersion:\s+v3\.1\.1\s*$", version.stdout)
        is not None,
        "DEPENDENCY_EVIDENCE",
        "Cosign 3.1.1 is required for retained bundle reverification",
    )
    _run_cosign(
        root,
        [
            "verify-blob",
            "--bundle",
            cosign_bundle.as_posix(),
            "--certificate-identity",
            POLARIS_DEPENDENCY_PUBLISHER_IDENTITY,
            "--certificate-oidc-issuer",
            POLARIS_DEPENDENCY_PUBLISHER_ISSUER,
            "--certificate-github-workflow-repository",
            POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
            "--certificate-github-workflow-ref",
            POLARIS_DEPENDENCY_PUBLISHER_REF,
            "--certificate-github-workflow-sha",
            POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA,
            "--certificate-github-workflow-trigger",
            POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
            manifest.as_posix(),
        ],
        "signature-bundle verification",
    )
    with tempfile.TemporaryDirectory(
        prefix="polaris-slsa-bundle-"
    ) as directory:
        bundle_path = Path(directory) / "bundle.json"
        try:
            bundle_path.write_text(
                json.dumps(nested_bundle, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError as error:
            _fail(
                "DEPENDENCY_EVIDENCE",
                f"cannot stage retained SLSA bundle: {error}",
            )
        _run_cosign(
            root,
            [
                "verify-blob-attestation",
                "--bundle",
                bundle_path.as_posix(),
                "--type",
                "slsaprovenance1",
                "--certificate-identity",
                POLARIS_DEPENDENCY_PUBLISHER_IDENTITY,
                "--certificate-oidc-issuer",
                POLARIS_DEPENDENCY_PUBLISHER_ISSUER,
                "--certificate-github-workflow-repository",
                POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
                "--certificate-github-workflow-ref",
                POLARIS_DEPENDENCY_PUBLISHER_REF,
                "--certificate-github-workflow-sha",
                POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA,
                "--certificate-github-workflow-trigger",
                POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
                manifest.as_posix(),
            ],
            "SLSA-bundle verification",
        )
    _VERIFIED_DEPENDENCY_CRYPTOGRAPHIC_BINDINGS.add(binding)


def _audit_dependency_sigstore(
    root: Path,
    slsa_document: Any,
    dependency_crypto_verifier: DependencyCryptoVerifier,
) -> None:
    manifest = POLARIS_EVIDENCE / "oci-manifest.json"
    cosign_bundle = POLARIS_EVIDENCE / "cosign-signature-bundle.json"
    _expect(
        isinstance(slsa_document, list)
        and len(slsa_document) == 1
        and isinstance(slsa_document[0], Mapping)
        and isinstance(
            _nested(slsa_document[0], "attestation", "bundle"),
            Mapping,
        ),
        "DEPENDENCY_EVIDENCE",
        "SLSA verification must retain exactly one Sigstore bundle",
    )
    nested_bundle = _nested(
        slsa_document[0],
        "attestation",
        "bundle",
    )
    dependency_crypto_verifier(
        root,
        manifest,
        cosign_bundle,
        nested_bundle,
    )


def _audit_dependency_slsa(root: Path) -> Any:
    relative = POLARIS_EVIDENCE / "slsa-verify.json"
    document = _load_json_value(root, relative, "DEPENDENCY_EVIDENCE")
    _expect(
        isinstance(document, list)
        and len(document) == 1
        and isinstance(document[0], Mapping),
        "DEPENDENCY_EVIDENCE",
        "SLSA verification must contain exactly one result",
    )
    result = document[0]
    _expect(
        set(result) == {"attestation", "verificationResult"}
        and isinstance(result["attestation"], Mapping)
        and isinstance(result["verificationResult"], Mapping),
        "DEPENDENCY_EVIDENCE",
        "SLSA verification result structure changed",
    )
    statement = _nested(result, "verificationResult", "statement")
    bundle = _nested(result, "attestation", "bundle")
    _expect(
        isinstance(statement, Mapping) and isinstance(bundle, Mapping),
        "DEPENDENCY_EVIDENCE",
        "SLSA statement or Sigstore bundle is missing",
    )
    _expect(
        statement.get("_type") == "https://in-toto.io/Statement/v1"
        and statement.get("predicateType")
        == "https://slsa.dev/provenance/v1"
        and statement.get("subject")
        == [
            {
                "name": (
                    "ghcr.io/tommykammy/"
                    "shirokuma-polaris-gradle-dependencies"
                ),
                "digest": {
                    "sha256": POLARIS_DEPENDENCY_MANIFEST_SHA256
                },
            }
        ],
        "DEPENDENCY_EVIDENCE",
        "SLSA subject does not bind the exact dependency manifest",
    )
    expected_predicate = {
        "buildDefinition": {
            "buildType": "https://actions.github.io/buildtypes/workflow/v1",
            "externalParameters": {
                "workflow": {
                    "path": ".github/workflows/polaris-gradle-dependencies.yml",
                    "ref": POLARIS_DEPENDENCY_PUBLISHER_REF,
                    "repository": (
                        POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY_URL
                    ),
                }
            },
            "internalParameters": {
                "github": {
                    "event_name": POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
                    "repository_id": "1289807958",
                    "repository_owner_id": "257892020",
                    "runner_environment": "github-hosted",
                }
            },
            "resolvedDependencies": [
                {
                    "digest": {
                        "gitCommit": POLARIS_DEPENDENCY_SOURCE_SHA
                    },
                    "uri": (
                        "git+https://github.com/TommyKammy/Shirokuma"
                        "@refs/heads/main"
                    ),
                }
            ],
        },
        "runDetails": {
            "builder": {"id": POLARIS_DEPENDENCY_PUBLISHER_IDENTITY},
            "metadata": {
                "invocationId": (
                    "https://github.com/TommyKammy/Shirokuma/actions/runs/"
                    f"{POLARIS_DEPENDENCY_RUN_ID}/attempts/"
                    f"{POLARIS_DEPENDENCY_RUN_ATTEMPT}"
                )
            },
        },
    }
    _expect(
        statement.get("predicate") == expected_predicate,
        "DEPENDENCY_EVIDENCE",
        "SLSA workflow provenance differs from the reviewed main run",
    )
    envelope = _nested(bundle, "dsseEnvelope")
    _expect(
        isinstance(envelope, Mapping),
        "DEPENDENCY_EVIDENCE",
        "SLSA Sigstore bundle lacks a DSSE envelope",
    )
    decoded = _decoded_dsse_payload(
        envelope,
        relative.as_posix(),
    )
    _expect(
        decoded == statement,
        "DEPENDENCY_EVIDENCE",
        "SLSA DSSE payload differs from the verified statement",
    )
    return document


def _audit_dependency_publication_evidence(
    root: Path,
    contract: Mapping[str, Any],
    dependency_crypto_verifier: DependencyCryptoVerifier,
) -> None:
    directory = root / POLARIS_EVIDENCE
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "DEPENDENCY_EVIDENCE",
        "dependency evidence root must be a real directory",
    )
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expected = {"README.md", *POLARIS_DEPENDENCY_EVIDENCE_RECORDS}
    _expect(
        actual == expected,
        "DEPENDENCY_EVIDENCE",
        "dependency evidence inventory must be closed; "
        f"expected {sorted(expected)}, found {sorted(actual)}",
    )
    for filename in expected:
        relative = POLARIS_EVIDENCE / filename
        _expect(
            _is_regular_file_without_symlink_components(root, relative),
            "DEPENDENCY_EVIDENCE",
            f"dependency evidence must be a real regular file: {relative}",
        )
    for filename in POLARIS_DEPENDENCY_EVIDENCE_RECORDS:
        _audit_dependency_evidence_file(root, filename)

    publication = _load_json_value(
        root,
        POLARIS_EVIDENCE / "publication.json",
        "DEPENDENCY_EVIDENCE",
    )
    _expect(
        isinstance(publication, Mapping),
        "DEPENDENCY_EVIDENCE",
        "publication evidence must be a JSON object",
    )
    record_fields = {
        "archive",
        "cosign_bundle",
        "cosign_verification",
        "descriptor",
        "manifest",
        "offline_build",
        "slsa_verification",
        "toolchain",
        "verification_metadata",
    }
    _expect_keysets(
        publication,
        {
            (): {
                "admitted",
                "anonymous_pull",
                "archive",
                "cosign_bundle",
                "cosign_verification",
                "created",
                "descriptor",
                "manifest",
                "offline_build",
                "reference",
                "schema_version",
                "slsa_verification",
                "state",
                "tag",
                "toolchain",
                "verification_metadata",
                "workflow",
            },
            **{
                (field,): {"filename", "sha256", "size"}
                for field in record_fields
            },
            ("workflow",): {
                "event",
                "ref",
                "repository",
                "run_attempt",
                "run_id",
                "source_sha",
                "workflow_sha",
            },
        },
        "DEPENDENCY_EVIDENCE",
    )
    _expect_fields(
        publication,
        {
            ("schema_version",): 1,
            ("state",): "dependency_snapshot_review_pending",
            ("admitted",): False,
            ("anonymous_pull",): True,
            ("created",): "2026-07-19T22:30:37+09:00",
            ("reference",): POLARIS_DEPENDENCY_REFERENCE,
            (
                "tag",
            ): (
                "ghcr.io/tommykammy/"
                "shirokuma-polaris-gradle-dependencies:"
                "1.6.0-29689013375-1"
            ),
            ("workflow", "event"): POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
            ("workflow", "ref"): POLARIS_DEPENDENCY_PUBLISHER_REF,
            (
                "workflow",
                "repository",
            ): POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
            ("workflow", "run_attempt"): POLARIS_DEPENDENCY_RUN_ATTEMPT,
            ("workflow", "run_id"): POLARIS_DEPENDENCY_RUN_ID,
            ("workflow", "source_sha"): POLARIS_DEPENDENCY_SOURCE_SHA,
            ("workflow", "workflow_sha"): POLARIS_DEPENDENCY_SOURCE_SHA,
        },
        "DEPENDENCY_EVIDENCE",
    )
    publication_records = {
        "cosign_bundle": "cosign-signature-bundle.json",
        "cosign_verification": "cosign-verify.json",
        "descriptor": "gradle-dependency-inputs.json",
        "manifest": "oci-manifest.json",
        "offline_build": "offline-build.json",
        "slsa_verification": "slsa-verify.json",
        "toolchain": "toolchain.json",
        "verification_metadata": "verification-metadata.xml",
    }
    for field, filename in publication_records.items():
        expected_sha256, expected_size = (
            POLARIS_DEPENDENCY_EVIDENCE_RECORDS[filename]
        )
        _expect(
            publication[field]
            == {
                "filename": filename,
                "sha256": expected_sha256,
                "size": expected_size,
            },
            "DEPENDENCY_EVIDENCE",
            f"publication {field} record differs from retained bytes",
        )
    _expect(
        publication["archive"]
        == {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "sha256": POLARIS_DEPENDENCY_ARCHIVE_SHA256,
            "size": POLARIS_DEPENDENCY_ARCHIVE_SIZE,
        },
        "DEPENDENCY_EVIDENCE",
        "publication archive record changed",
    )
    _expect(
        _nested(
            contract,
            "dependency_snapshot",
            "publication",
            "record",
            "sha256",
        )
        == POLARIS_DEPENDENCY_PUBLICATION_SHA256
        and _nested(
            contract,
            "dependency_snapshot",
            "artifact_reference",
        )
        == publication["reference"],
        "DEPENDENCY_EVIDENCE",
        "contract and retained publication evidence differ",
    )

    _audit_dependency_descriptor(root, publication)
    _audit_dependency_oci_manifest(root, publication)

    offline = _load_json_value(
        root,
        POLARIS_EVIDENCE / "offline-build.json",
        "DEPENDENCY_EVIDENCE",
    )
    _expect(
        offline
        == {
            "archive_sha256": POLARIS_DEPENDENCY_ARCHIVE_SHA256,
            "build_cache": False,
            "configuration_cache": False,
            "dependency_verification": "strict",
            "descriptor_sha256": POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "gradle-dependency-inputs.json"
            ][0],
            "gradle_offline": True,
            "network": "none",
            "platform": "linux/arm64",
            "result": "passed",
            "schema_version": 1,
            "tasks": POLARIS_SERVER_TASKS,
            "verification_metadata_sha256": (
                POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                    "verification-metadata.xml"
                ][0]
            ),
        },
        "DEPENDENCY_EVIDENCE",
        "offline build proof is not the reviewed closed build",
    )
    toolchain = _load_json_value(
        root,
        POLARIS_EVIDENCE / "toolchain.json",
        "DEPENDENCY_EVIDENCE",
    )
    _expect(
        isinstance(toolchain, Mapping)
        and set(toolchain)
        == {
            "builder_image",
            "curl",
            "docker",
            "gpg",
            "gradle",
            "java",
            "platform",
            "python",
            "runner",
            "schema_version",
            "tar",
        },
        "DEPENDENCY_EVIDENCE",
        "dependency resolver toolchain record structure changed",
    )
    _expect_fields(
        toolchain,
        {
            ("schema_version",): 1,
            ("builder_image",): BUILDER_ARM64,
            ("runner",): "ubuntu-24.04-arm",
            ("platform",): (
                "Linux-6.17.0-1020-azure-aarch64-with-glibc2.39"
            ),
            ("gradle",): "9.6.0",
            ("java",): "21.0.11",
            ("python",): "3.12.3",
            ("docker",): "28.0.4",
            ("tar",): "tar (GNU tar) 1.35",
            ("gpg",): "gpg (GnuPG) 2.4.4",
        },
        "DEPENDENCY_EVIDENCE",
    )
    registry_verification = _load_json_value(
        root,
        POLARIS_EVIDENCE / "cosign-verify.json",
        "DEPENDENCY_EVIDENCE",
    )
    _expect(
        registry_verification
        == [
            {
                "critical": {
                    "identity": {
                        "docker-reference": POLARIS_DEPENDENCY_REFERENCE
                    },
                    "image": {
                        "docker-manifest-digest": (
                            "sha256:"
                            + POLARIS_DEPENDENCY_MANIFEST_SHA256
                        )
                    },
                    "type": "https://sigstore.dev/cosign/sign/v1",
                },
                "optional": {},
            }
        ],
        "DEPENDENCY_EVIDENCE",
        "retained registry verification does not bind the exact reference",
    )
    slsa_document = _audit_dependency_slsa(root)
    _audit_dependency_sigstore(
        root,
        slsa_document,
        dependency_crypto_verifier,
    )


ImageCryptoVerifier = Callable[[Path, Mapping[str, Any]], None]


def _load_jsonl(
    root: Path,
    relative: Path,
    code: str,
) -> list[Mapping[str, Any]]:
    _expect(
        _is_regular_file_without_symlink_components(root, relative),
        code,
        f"evidence must be a real regular file: {relative}",
    )
    try:
        lines = (root / relative).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        _fail(code, f"cannot read {relative}: {error}")
    _expect(bool(lines), code, f"{relative} must not be empty")
    records: list[Mapping[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        _expect(bool(line), code, f"{relative}:{index} must not be blank")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_pairs,
            )
        except ValueError as error:
            _fail(code, f"cannot read {relative}:{index}: {error}")
        _expect(
            isinstance(value, Mapping),
            code,
            f"{relative}:{index} must be a JSON object",
        )
        records.append(value)
    return records


def _decode_image_dsse_statement(
    bundle: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    _expect(
        bundle.get("mediaType")
        == "application/vnd.dev.sigstore.bundle.v0.3+json",
        "IMAGE_EVIDENCE",
        f"{name} uses an unexpected Sigstore bundle media type",
    )
    material = bundle.get("verificationMaterial")
    _expect(
        isinstance(material, Mapping)
        and isinstance(material.get("certificate"), Mapping)
        and isinstance(material.get("tlogEntries"), list)
        and len(material["tlogEntries"]) == 1,
        "IMAGE_EVIDENCE",
        f"{name} lacks the retained certificate or transparency entry",
    )
    envelope = bundle.get("dsseEnvelope")
    _expect(
        isinstance(envelope, Mapping)
        and envelope.get("payloadType") == "application/vnd.in-toto+json"
        and isinstance(envelope.get("signatures"), list)
        and len(envelope["signatures"]) == 1,
        "IMAGE_EVIDENCE",
        f"{name} lacks the exact DSSE envelope",
    )
    encoded = envelope.get("payload")
    _expect(
        isinstance(encoded, str),
        "IMAGE_EVIDENCE",
        f"{name} DSSE payload is missing",
    )
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        _fail("IMAGE_EVIDENCE", f"{name} DSSE payload is invalid: {error}")
    _expect(
        len(payload) <= MAX_DSSE_PAYLOAD_BYTES,
        "IMAGE_EVIDENCE",
        f"{name} DSSE payload exceeds the reviewed bound",
    )
    try:
        statement = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except ValueError as error:
        _fail("IMAGE_EVIDENCE", f"{name} DSSE statement is invalid: {error}")
    _expect(
        isinstance(statement, Mapping),
        "IMAGE_EVIDENCE",
        f"{name} DSSE statement must be an object",
    )
    return statement


def _expect_image_statement(
    statement: Mapping[str, Any],
    predicate_type: str,
    name: str,
) -> None:
    if predicate_type == "https://sigstore.dev/cosign/sign/v1":
        expected_subject = [
            {
                "digest": {
                    "sha256": POLARIS_IMAGE_DIGEST.removeprefix("sha256:")
                },
                "annotations": {},
            }
        ]
    else:
        expected_subject = [
            {
                "name": "ghcr.io/tommykammy/shirokuma-polaris",
                "digest": {
                    "sha256": POLARIS_IMAGE_DIGEST.removeprefix("sha256:")
                },
            }
        ]
    _expect(
        statement.get("_type")
        in {
            "https://in-toto.io/Statement/v0.1",
            "https://in-toto.io/Statement/v1",
        }
        and statement.get("predicateType") == predicate_type
        and statement.get("subject") == expected_subject,
        "IMAGE_EVIDENCE",
        f"{name} does not bind the exact Polaris image and predicate",
    )


def _audit_image_release_record(
    root: Path,
) -> Mapping[str, Any]:
    actual_sha256, _ = _sha256_and_size(
        root,
        POLARIS_RELEASE_EVIDENCE,
        "IMAGE_EVIDENCE",
    )
    _expect(
        actual_sha256 == POLARIS_RELEASE_EVIDENCE_SHA256,
        "IMAGE_EVIDENCE",
        "release-evidence.json differs from the reviewed checkpoint",
    )
    release = _load_json(
        root,
        POLARIS_RELEASE_EVIDENCE,
        "IMAGE_EVIDENCE",
    )
    _expect_keysets(
        release,
        {
            (): {
                "schema_version",
                "component",
                "version",
                "platform",
                "state",
                "admitted",
                "reference",
                "digest",
                "trusted_tag",
                "trusted_tag_role",
                "atomic_admission_receipt",
                "publisher_checkpoint",
                "source",
                "build_inputs",
                "publication",
                "vulnerabilities",
                "sbom",
                "runtime_smoke",
                "actions_artifacts",
                "evidence",
                "next_boundary",
            },
            ("atomic_admission_receipt",): {"path", "sha256"},
            ("publisher_checkpoint",): {
                "repository",
                "workflow",
                "workflow_file_sha256",
                "ref",
                "event",
                "source_sha",
                "workflow_sha",
                "run_id",
                "run_attempt",
                "build_contract",
                "admission",
                "retired",
            },
            ("publisher_checkpoint", "build_contract"): {"path", "sha256"},
            ("publisher_checkpoint", "admission"): {"path", "sha256"},
            ("source",): {
                "record",
                "record_sha256",
                "archive_sha512",
                "commit",
                "tree",
            },
            ("build_inputs",): {
                "containerfile",
                "bounded_runtime_patch",
                "dependency_reference",
                "runtime_base",
            },
            ("build_inputs", "containerfile"): {"path", "sha256"},
            ("build_inputs", "bounded_runtime_patch"): {"path", "sha256"},
            ("publication",): {
                "record",
                "record_sha256",
                "created",
                "promotion_completed_at",
                "anonymous_pull",
                "promotion_anonymous_verification",
                "slsa_provenance",
            },
            ("vulnerabilities",): {"high", "critical"},
            ("sbom",): {
                "format",
                "spec_version",
                "component_count",
                "forbidden_component_terms",
                "matching_component_count",
            },
            ("runtime_smoke",): {
                "result",
                "user",
                "read_only_rootfs",
                "capabilities_dropped",
                "no_new_privileges",
                "readiness_endpoint",
                "readiness_status",
                "raw_log_retained",
                "sanitized_log_retained",
            },
            ("actions_artifacts",): {"build_input", "candidate", "final"},
            ("actions_artifacts", "build_input"): {
                "id",
                "name",
                "sha256",
                "size",
                "retention_days",
            },
            ("actions_artifacts", "candidate"): {
                "id",
                "name",
                "sha256",
                "size",
                "retention_days",
            },
            ("actions_artifacts", "final"): {
                "id",
                "name",
                "sha256",
                "size",
                "retention_days",
            },
            ("evidence",): {"directory", "self_manifest", "records"},
            ("evidence", "self_manifest"): {
                "path",
                "sha256",
                "size",
                "entries",
            },
            ("next_boundary",): {
                "state",
                "atomic_peer",
                "resident_ledger_permitted",
                "runtime_permitted",
            },
        },
        "IMAGE_EVIDENCE",
    )
    _expect_fields(
        release,
        {
            ("schema_version",): 2,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("state",): "admitted",
            ("admitted",): True,
            ("reference",): POLARIS_IMAGE_REFERENCE,
            ("digest",): POLARIS_IMAGE_DIGEST,
            ("trusted_tag",): POLARIS_IMAGE_TRUSTED_TAG,
            ("trusted_tag_role",): "non_authoritative_pointer",
            (
                "atomic_admission_receipt",
                "path",
            ): POLARIS_ATOMIC_ADMISSION.as_posix(),
            (
                "atomic_admission_receipt",
                "sha256",
            ): POLARIS_ATOMIC_ADMISSION_SHA256,
            (
                "publisher_checkpoint",
                "repository",
            ): POLARIS_IMAGE_PUBLISHER_REPOSITORY,
            (
                "publisher_checkpoint",
                "workflow",
            ): POLARIS_IMAGE_WORKFLOW.as_posix(),
            (
                "publisher_checkpoint",
                "workflow_file_sha256",
            ): POLARIS_IMAGE_WORKFLOW_SHA256,
            (
                "publisher_checkpoint",
                "ref",
            ): POLARIS_IMAGE_PUBLISHER_REF,
            (
                "publisher_checkpoint",
                "event",
            ): POLARIS_IMAGE_PUBLISHER_TRIGGER,
            (
                "publisher_checkpoint",
                "source_sha",
            ): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            (
                "publisher_checkpoint",
                "workflow_sha",
            ): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            (
                "publisher_checkpoint",
                "run_id",
            ): POLARIS_IMAGE_PUBLISHER_RUN_ID,
            (
                "publisher_checkpoint",
                "run_attempt",
            ): POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT,
            (
                "publisher_checkpoint",
                "build_contract",
                "path",
            ): POLARIS_CONTRACT.as_posix(),
            (
                "publisher_checkpoint",
                "build_contract",
                "sha256",
            ): POLARIS_IMAGE_PUBLISHER_CONTRACT_SHA256,
            (
                "publisher_checkpoint",
                "admission",
                "path",
            ): POLARIS_ADMISSION.as_posix(),
            (
                "publisher_checkpoint",
                "admission",
                "sha256",
            ): POLARIS_IMAGE_PUBLISHER_ADMISSION_SHA256,
            ("publisher_checkpoint", "retired"): True,
            ("source", "record"): POLARIS_SOURCE.as_posix(),
            ("source", "record_sha256"): POLARIS_SOURCE_SHA256,
            ("source", "archive_sha512"): POLARIS_ARCHIVE_SHA512,
            ("source", "commit"): POLARIS_COMMIT,
            ("source", "tree"): POLARIS_TREE,
            (
                "build_inputs",
                "containerfile",
                "path",
            ): POLARIS_CONTAINERFILE.as_posix(),
            (
                "build_inputs",
                "containerfile",
                "sha256",
            ): POLARIS_CONTAINERFILE_SHA256,
            (
                "build_inputs",
                "bounded_runtime_patch",
                "path",
            ): POLARIS_SOURCE_OVERLAY.as_posix(),
            (
                "build_inputs",
                "bounded_runtime_patch",
                "sha256",
            ): POLARIS_SOURCE_OVERLAY_SHA256,
            (
                "build_inputs",
                "dependency_reference",
            ): POLARIS_DEPENDENCY_REFERENCE,
            ("build_inputs", "runtime_base"): IMAGE_RUNTIME_ARM64,
            (
                "publication",
                "record",
            ): (POLARIS_IMAGE_EVIDENCE / "publication.json").as_posix(),
            (
                "publication",
                "record_sha256",
            ): "b620e2d752a93e9d0cf1a945e6ee820c0229eddb6033e2dc104086a40299d37c",
            ("publication", "created"): POLARIS_IMAGE_CREATED,
            (
                "publication",
                "promotion_completed_at",
            ): POLARIS_IMAGE_PROMOTION_COMPLETED_AT,
            ("publication", "anonymous_pull"): True,
            ("publication", "promotion_anonymous_verification"): True,
            (
                "publication",
                "slsa_provenance",
            ): POLARIS_IMAGE_SLSA_PROVENANCE,
            ("vulnerabilities", "high"): 0,
            ("vulnerabilities", "critical"): 0,
            ("sbom", "format"): "CycloneDX",
            ("sbom", "spec_version"): "1.7",
            ("sbom", "component_count"): 6_731,
            (
                "sbom",
                "forbidden_component_terms",
            ): ["hadoop", "ranger", "jetty-http"],
            ("sbom", "matching_component_count"): 0,
            ("runtime_smoke", "result"): "passed",
            ("runtime_smoke", "user"): "10000:10001",
            ("runtime_smoke", "read_only_rootfs"): True,
            ("runtime_smoke", "capabilities_dropped"): "ALL",
            ("runtime_smoke", "no_new_privileges"): True,
            ("runtime_smoke", "readiness_endpoint"): "/q/health/ready",
            ("runtime_smoke", "readiness_status"): "UP",
            ("runtime_smoke", "raw_log_retained"): False,
            ("runtime_smoke", "sanitized_log_retained"): False,
            ("evidence", "directory"): POLARIS_IMAGE_EVIDENCE.as_posix(),
            (
                "evidence",
                "self_manifest",
                "path",
            ): (POLARIS_IMAGE_EVIDENCE / "evidence.sha256").as_posix(),
            (
                "evidence",
                "self_manifest",
                "sha256",
            ): POLARIS_IMAGE_EVIDENCE_MANIFEST_SHA256,
            (
                "evidence",
                "self_manifest",
                "size",
            ): POLARIS_IMAGE_EVIDENCE_MANIFEST_SIZE,
            ("evidence", "self_manifest", "entries"): 32,
            ("next_boundary", "state"): "runtime_acceptance_pending",
            (
                "next_boundary",
                "atomic_peer",
            ): POSTGRES_ADMISSION.as_posix(),
            ("next_boundary", "resident_ledger_permitted"): True,
            ("next_boundary", "runtime_permitted"): False,
        },
        "IMAGE_EVIDENCE",
    )
    expected_actions = {
        "build_input": {
            "id": "8449152758",
            "name": (
                "polaris-1.6.0-arm64-build-input-29711984394-1"
            ),
            "sha256": (
                "41a10eb6eeb46691d28c74262b3698baf14a7d9b31b0c960e4844b541ef2b657"
            ),
            "size": 148_344_354,
            "retention_days": 7,
        },
        "candidate": {
            "id": "8449174814",
            "name": "polaris-1.6.0-arm64-candidate-29711984394-1",
            "sha256": (
                "73097c25794a8e58b46bad453236065cce39f38eece3c2647044d5cd910f98de"
            ),
            "size": 1_754_984,
            "retention_days": 30,
        },
        "final": {
            "id": "8449181390",
            "name": "polaris-image-publication-29711984394-1",
            "sha256": (
                "97c413927e024ff5687350b75ee172a5a890e5423292ce9c6942fd1663d3121e"
            ),
            "size": 1_764_175,
            "retention_days": 30,
        },
    }
    _expect(
        release.get("actions_artifacts") == expected_actions,
        "IMAGE_EVIDENCE",
        "GitHub Actions artifact metadata differs from the successful run",
    )
    return release


def _audit_image_evidence_inventory(
    root: Path,
    release: Mapping[str, Any],
) -> None:
    records = _nested(release, "evidence", "records")
    _expect(
        isinstance(records, Mapping)
        and set(records) == POLARIS_IMAGE_EVIDENCE_REQUIRED,
        "IMAGE_EVIDENCE",
        "release evidence payload inventory must be the exact 32-file closure",
    )
    directory = root / POLARIS_IMAGE_EVIDENCE
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "IMAGE_EVIDENCE",
        "retained image evidence directory is invalid",
    )
    actual_names: set[str] = set()
    for path in directory.iterdir():
        _expect(
            path.is_file() and not path.is_symlink(),
            "IMAGE_EVIDENCE",
            f"retained image evidence must be a regular file: {path.name}",
        )
        actual_names.add(path.name)
    _expect(
        actual_names
        == POLARIS_IMAGE_EVIDENCE_REQUIRED | {"evidence.sha256"},
        "IMAGE_EVIDENCE",
        "retained image evidence inventory must be closed",
    )
    observed_hashes: dict[str, str] = {}
    for name in sorted(POLARIS_IMAGE_EVIDENCE_REQUIRED):
        metadata = records.get(name)
        _expect(
            isinstance(metadata, Mapping)
            and set(metadata) == {"sha256", "size"}
            and isinstance(metadata.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
            is not None
            and isinstance(metadata.get("size"), int)
            and metadata["size"] > 0,
            "IMAGE_EVIDENCE",
            f"invalid release evidence metadata for {name}",
        )
        actual = _sha256_and_size(
            root,
            POLARIS_IMAGE_EVIDENCE / name,
            "IMAGE_EVIDENCE",
        )
        _expect(
            actual == (metadata["sha256"], metadata["size"]),
            "IMAGE_EVIDENCE",
            f"{name} differs from release-evidence.json",
        )
        observed_hashes[name] = actual[0]

    manifest_relative = POLARIS_IMAGE_EVIDENCE / "evidence.sha256"
    manifest_hash, manifest_size = _sha256_and_size(
        root,
        manifest_relative,
        "IMAGE_EVIDENCE",
    )
    _expect(
        (
            manifest_hash,
            manifest_size,
        )
        == (
            POLARIS_IMAGE_EVIDENCE_MANIFEST_SHA256,
            POLARIS_IMAGE_EVIDENCE_MANIFEST_SIZE,
        ),
        "IMAGE_EVIDENCE",
        "publisher self-manifest differs from the reviewed artifact",
    )
    try:
        lines = (root / manifest_relative).read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as error:
        _fail("IMAGE_EVIDENCE", f"cannot read publisher self-manifest: {error}")
    parsed: dict[str, str] = {}
    ordered_names: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  \./([A-Za-z0-9._-]+)", line)
        _expect(
            match is not None,
            "IMAGE_EVIDENCE",
            "publisher self-manifest contains a noncanonical record",
        )
        assert match is not None
        name = match.group(2)
        _expect(
            name not in parsed,
            "IMAGE_EVIDENCE",
            f"publisher self-manifest duplicates {name}",
        )
        parsed[name] = match.group(1)
        ordered_names.append(name)
    _expect(
        set(parsed) == POLARIS_IMAGE_EVIDENCE_REQUIRED
        and ordered_names == sorted(ordered_names)
        and parsed == observed_hashes,
        "IMAGE_EVIDENCE",
        "publisher self-manifest does not close the exact payload inventory",
    )


def _audit_image_manifest_and_runtime(
    root: Path,
    release: Mapping[str, Any],
) -> None:
    manifest_paths = [
        POLARIS_IMAGE_EVIDENCE / "image-manifest.json",
        POLARIS_IMAGE_EVIDENCE / "anonymous-image-manifest.json",
        POLARIS_IMAGE_EVIDENCE / "trusted-tag-manifest.json",
    ]
    try:
        manifest_bytes = [(root / path).read_bytes() for path in manifest_paths]
    except OSError as error:
        _fail("IMAGE_EVIDENCE", f"cannot read retained image manifests: {error}")
    _expect(
        manifest_bytes[0] == manifest_bytes[1] == manifest_bytes[2],
        "IMAGE_EVIDENCE",
        "candidate, anonymous, and trusted-tag manifests must be byte-identical",
    )
    _expect(
        hashlib.sha256(manifest_bytes[0]).hexdigest()
        == POLARIS_IMAGE_DIGEST.removeprefix("sha256:"),
        "IMAGE_EVIDENCE",
        "retained manifest bytes do not equal the immutable image digest",
    )
    manifest = _load_json_value(
        root,
        manifest_paths[0],
        "IMAGE_EVIDENCE",
    )
    layers = manifest.get("layers") if isinstance(manifest, Mapping) else None
    _expect(
        isinstance(manifest, Mapping)
        and manifest.get("schemaVersion") == 2
        and manifest.get("mediaType")
        == "application/vnd.oci.image.manifest.v1+json"
        and isinstance(manifest.get("config"), Mapping)
        and isinstance(layers, list)
        and len(layers) == 9
        and all(
            isinstance(layer, Mapping)
            and set(layer) == {"mediaType", "digest", "size"}
            and layer.get("mediaType")
            == "application/vnd.oci.image.layer.v1.tar+gzip"
            and isinstance(layer.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", layer["digest"])
            is not None
            and type(layer.get("size")) is int
            and layer["size"] > 0
            for layer in layers
        ),
        "IMAGE_EVIDENCE",
        "retained OCI image manifest structure changed",
    )
    config_path = POLARIS_IMAGE_EVIDENCE / "image-config.json"
    config_hash, config_size = _sha256_and_size(
        root,
        config_path,
        "IMAGE_EVIDENCE",
    )
    _expect(
        manifest["config"]
        == {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + config_hash,
            "size": config_size,
        },
        "IMAGE_EVIDENCE",
        "OCI manifest does not bind the retained image config",
    )
    config = _load_json_value(root, config_path, "IMAGE_EVIDENCE")
    _expect(
        isinstance(config, Mapping)
        and config.get("architecture") == "arm64"
        and config.get("os") == "linux"
        and _nested(config, "config", "User") == "10000:10001"
        and _nested(config, "config", "Entrypoint") == ["/usr/bin/java"]
        and _nested(config, "config", "Cmd")
        == ["-jar", "/deployments/quarkus-run.jar"]
        and _nested(config, "config", "WorkingDir") == "/deployments"
        and _nested(
            config,
            "config",
            "Labels",
            "dev.shirokuma.runtime-base.arm64-digest",
        )
        == IMAGE_RUNTIME_ARM64.removeprefix("docker.io/library/amazoncorretto@"),
        "IMAGE_EVIDENCE",
        "retained image config violates platform, command, or user policy",
    )
    rootfs = config.get("rootfs") if isinstance(config, Mapping) else None
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, Mapping) else None
    _expect(
        isinstance(rootfs, Mapping)
        and rootfs.get("type") == "layers"
        and isinstance(diff_ids, list)
        and len(diff_ids) == len(layers)
        and all(
            isinstance(diff_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", diff_id) is not None
            for diff_id in diff_ids
        ),
        "IMAGE_EVIDENCE",
        "retained image config rootfs layer chain changed",
    )
    runtime_base_path = POLARIS_IMAGE_EVIDENCE / "runtime-base-manifest.json"
    _expect(
        _sha256(root / runtime_base_path)
        == IMAGE_RUNTIME_ARM64.rsplit("sha256:", 1)[1],
        "IMAGE_EVIDENCE",
        "runtime base manifest does not equal the pinned arm64 digest",
    )
    try:
        java_version = (
            root
            / POLARIS_IMAGE_EVIDENCE
            / "runtime-base-java-version.txt"
        ).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail("IMAGE_EVIDENCE", f"cannot read runtime Java evidence: {error}")
    _expect(
        'openjdk version "21.0.11"' in java_version
        and "Corretto" in java_version,
        "IMAGE_EVIDENCE",
        "runtime base Java evidence changed",
    )

    runtime_inspect = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "runtime-container-inspect.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        _json_equal_type_sensitive(
            runtime_inspect,
            {
            "args": ["-jar", "/deployments/quarkus-run.jar"],
            "capabilities_dropped": ["ALL"],
            "memory_bytes": 1_610_612_736,
            "path": "/usr/bin/java",
            "pids_limit": 512,
            "read_only_rootfs": True,
            "reference": POLARIS_IMAGE_REFERENCE,
            "schema_version": 1,
            "security_options": ["no-new-privileges"],
            "tmpfs": {
                "/tmp": [
                    "gid=10001",
                    "mode=1777",
                    "nodev",
                    "nosuid",
                    "rw",
                    "size=64m",
                    "uid=10000",
                ]
            },
                "user": "10000:10001",
            },
        ),
        "IMAGE_EVIDENCE",
        "runtime container hardening evidence changed",
    )
    runtime_smoke = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "runtime-smoke.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        _json_equal_type_sensitive(
            runtime_smoke,
            {
            "capabilities_dropped": "ALL",
            "command": [
                "/usr/bin/java",
                "-jar",
                "/deployments/quarkus-run.jar",
            ],
            "no_new_privileges": True,
            "read_only_rootfs": True,
            "readiness_endpoint": "/q/health/ready",
            "readiness_status": "UP",
            "reference": POLARIS_IMAGE_REFERENCE,
            "result": "passed",
            "runtime_inspect_sha256": release["evidence"]["records"][
                "runtime-container-inspect.json"
            ]["sha256"],
            "schema_version": 1,
            "tmpfs": ["/tmp"],
                "user": "10000:10001",
            },
        ),
        "IMAGE_EVIDENCE",
        "runtime smoke evidence changed",
    )
    log_policy = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "runtime-smoke-log-policy.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        isinstance(log_policy, Mapping)
        and log_policy.get("schema_version") == 1
        and log_policy.get("result") == "passed"
        and log_policy.get("raw_log_retained") is False
        and log_policy.get("sanitized_log_retained") is False
        and log_policy.get("redaction_count") == 1
        and log_policy.get("redacted_fields")
        == ["polaris_root_principal_credentials"],
        "IMAGE_EVIDENCE",
        "runtime log retention or redaction policy changed",
    )
    health = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "health-ready.json",
        "IMAGE_EVIDENCE",
    )
    health_checks = health.get("checks") if isinstance(health, Mapping) else None
    _expect(
        isinstance(health, Mapping)
        and health.get("status") == "UP"
        and health_checks
        == [
            {
                "name": "MongoDB connection health check",
                "status": "UP",
            },
            {
                "name": "Database connections health check",
                "status": "UP",
            },
        ],
        "IMAGE_EVIDENCE",
        "retained readiness evidence is not healthy",
    )


def _audit_image_build_context(root: Path) -> None:
    relative = POLARIS_IMAGE_EVIDENCE / "build-context.sha256"
    try:
        text = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail("IMAGE_EVIDENCE", f"cannot read retained build context: {error}")
    _expect(
        text.endswith("\n") and "\r" not in text,
        "IMAGE_EVIDENCE",
        "retained build-context manifest must use canonical LF records",
    )
    lines = text.splitlines()
    _expect(
        len(lines) == 450,
        "IMAGE_EVIDENCE",
        "retained build-context manifest must close exactly 450 files",
    )
    records: dict[str, str] = {}
    paths: list[str] = []
    for line in lines:
        match = re.fullmatch(
            r"([0-9a-f]{64})  ([A-Za-z0-9._+@/-]+)",
            line,
        )
        _expect(
            match is not None,
            "IMAGE_EVIDENCE",
            "retained build-context manifest contains a noncanonical record",
        )
        assert match is not None
        digest, path = match.groups()
        parsed = PurePosixPath(path)
        _expect(
            not parsed.is_absolute()
            and parsed.as_posix() == path
            and "." not in parsed.parts
            and ".." not in parsed.parts
            and path not in records,
            "IMAGE_EVIDENCE",
            f"retained build-context path is unsafe or duplicated: {path}",
        )
        allowed = (
            path == "Containerfile"
            or path.startswith("build/quarkus-app/")
            or path in {"distribution/LICENSE", "distribution/NOTICE"}
        )
        _expect(
            allowed,
            "IMAGE_EVIDENCE",
            f"retained build-context path is outside the approved closure: {path}",
        )
        records[path] = digest
        paths.append(path)

    def order(path: str) -> tuple[int, int, str]:
        if path == "Containerfile":
            return (0, 0, path)
        if path.startswith("build/quarkus-app/"):
            remainder = path.removeprefix("build/quarkus-app/")
            return (1, 0 if "/" in remainder else 1, path)
        return (2, 0, path)

    _expect(
        paths == sorted(paths, key=order),
        "IMAGE_EVIDENCE",
        "retained build-context paths are not in canonical order",
    )
    _expect(
        records.get("Containerfile") == POLARIS_CONTAINERFILE_SHA256
        and {
            "build/quarkus-app/app/polaris-server-1.6.0.jar",
            "build/quarkus-app/quarkus-run.jar",
            "distribution/LICENSE",
            "distribution/NOTICE",
        }.issubset(records),
        "IMAGE_EVIDENCE",
        "retained build-context anchors or Containerfile hash changed",
    )
    forbidden_markers = ("hadoop", "ranger", "jetty-http")
    _expect(
        not any(
            path.lower().endswith(".jar")
            and any(marker in path.lower() for marker in forbidden_markers)
            for path in paths
        ),
        "IMAGE_EVIDENCE",
        "retained build-context contains a forbidden runtime JAR",
    )


def _audit_image_build_and_scan(
    root: Path,
    release: Mapping[str, Any],
) -> None:
    _audit_image_build_context(root)
    publication = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "publication.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        isinstance(publication, Mapping),
        "IMAGE_EVIDENCE",
        "publication.json must be an object",
    )
    _expect_fields(
        publication,
        {
            ("schema_version",): 1,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("reference",): POLARIS_IMAGE_REFERENCE,
            ("trusted_tag",): POLARIS_IMAGE_TRUSTED_TAG,
            ("candidate_tag",): POLARIS_IMAGE_CANDIDATE_TAG,
            ("trusted_tag_role",): "non_authoritative_pointer",
            ("state",): "image_evidence_review_pending",
            ("created",): POLARIS_IMAGE_CREATED,
            (
                "promotion_completed_at",
            ): POLARIS_IMAGE_PROMOTION_COMPLETED_AT,
            ("slsa_provenance",): POLARIS_IMAGE_SLSA_PROVENANCE,
            ("admitted",): False,
            ("promoted",): True,
            ("anonymous_pull",): True,
            ("promotion_anonymous_verification",): True,
            ("build_contract_sha256",): POLARIS_IMAGE_PUBLISHER_CONTRACT_SHA256,
            ("admission_sha256",): POLARIS_IMAGE_PUBLISHER_ADMISSION_SHA256,
            ("containerfile_sha256",): POLARIS_CONTAINERFILE_SHA256,
            ("bounded_runtime_patch_sha256",): POLARIS_SOURCE_OVERLAY_SHA256,
            ("dependency_reference",): POLARIS_DEPENDENCY_REFERENCE,
            ("runtime_base",): IMAGE_RUNTIME_ARM64,
            ("source_archive_sha512",): POLARIS_ARCHIVE_SHA512,
            ("source_commit",): POLARIS_COMMIT,
            ("workflow", "repository"): POLARIS_IMAGE_PUBLISHER_REPOSITORY,
            ("workflow", "ref"): POLARIS_IMAGE_PUBLISHER_REF,
            ("workflow", "event"): POLARIS_IMAGE_PUBLISHER_TRIGGER,
            ("workflow", "source_sha"): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            ("workflow", "workflow_sha"): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            ("workflow", "run_id"): POLARIS_IMAGE_PUBLISHER_RUN_ID,
            ("workflow", "run_attempt"): POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT,
        },
        "IMAGE_EVIDENCE",
    )
    _expect(
        publication.get("input_artifact_digest")
        == release["actions_artifacts"]["build_input"]["sha256"],
        "IMAGE_EVIDENCE",
        "publication does not bind the exact build-input artifact",
    )

    build_input = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "build-input.json",
        "IMAGE_EVIDENCE",
    )
    _expect_fields(
        build_input,
        {
            ("schema_version",): 1,
            ("repository",): POLARIS_IMAGE_PUBLISHER_REPOSITORY,
            ("ref",): POLARIS_IMAGE_PUBLISHER_REF,
            ("source_sha",): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            ("workflow_sha",): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            ("run_id",): POLARIS_IMAGE_PUBLISHER_RUN_ID,
            ("run_attempt",): POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT,
            ("state",): "image_publication_pending",
            ("admitted",): False,
            (
                "context_manifest_sha256",
            ): release["evidence"]["records"]["build-context.sha256"]["sha256"],
        },
        "IMAGE_EVIDENCE",
    )
    dependency_input = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "dependency-input.json",
        "IMAGE_EVIDENCE",
    )
    _expect_fields(
        dependency_input,
        {
            ("schema_version",): 1,
            ("result",): "passed",
            ("anonymous_pull",): True,
            ("reference",): POLARIS_DEPENDENCY_REFERENCE,
            (
                "manifest_sha256",
            ): POLARIS_DEPENDENCY_MANIFEST_SHA256,
            (
                "descriptor_sha256",
            ): POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "gradle-dependency-inputs.json"
            ][0],
            ("archive_sha256",): POLARIS_DEPENDENCY_ARCHIVE_SHA256,
            (
                "verification_metadata_sha256",
            ): POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                "verification-metadata.xml"
            ][0],
        },
        "IMAGE_EVIDENCE",
    )
    source_authentication = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "source-authentication.json",
        "IMAGE_EVIDENCE",
    )
    _expect_fields(
        source_authentication,
        {
            ("schema_version",): 1,
            ("result",): "passed",
            ("archive_sha512",): POLARIS_ARCHIVE_SHA512,
            ("source_commit",): POLARIS_COMMIT,
            ("signing_key_fingerprint",): POLARIS_KEY_FINGERPRINT,
        },
        "IMAGE_EVIDENCE",
    )
    offline = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "offline-build.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        isinstance(offline, Mapping)
        and offline.get("schema_version") == 1
        and offline.get("result") == "passed"
        and offline.get("platform") == "linux/arm64"
        and offline.get("network") == "none"
        and offline.get("gradle_offline") is True
        and offline.get("dependency_verification") == "strict"
        and offline.get("build_cache") is False
        and offline.get("configuration_cache") is False
        and offline.get("rerun_tasks") is True
        and offline.get("tasks") == POLARIS_SERVER_TASKS
        and offline.get("dependency_reference") == POLARIS_DEPENDENCY_REFERENCE
        and _nested(offline, "bounded_runtime_patch", "sha256")
        == POLARIS_SOURCE_OVERLAY_SHA256
        and _nested(offline, "bounded_runtime_patch", "forbidden_runtime_jars")
        == ["hadoop", "ranger", "jetty-http"],
        "IMAGE_EVIDENCE",
        "offline build or bounded-runtime proof changed",
    )

    sbom = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "polaris-1.6.0-arm64.cdx.json",
        "IMAGE_EVIDENCE",
    )
    components = sbom.get("components") if isinstance(sbom, Mapping) else None
    _expect(
        isinstance(sbom, Mapping)
        and sbom.get("bomFormat") == "CycloneDX"
        and sbom.get("specVersion") == "1.7"
        and isinstance(components, list)
        and len(components) == 6_731,
        "IMAGE_EVIDENCE",
        "retained CycloneDX SBOM format or component count changed",
    )
    _expect(
        _json_equal_type_sensitive(
            _nested(sbom, "metadata", "component"),
            {
                "bom-ref": "fa460d203894d3b3",
                "type": "container",
                "name": "ghcr.io/tommykammy/shirokuma-polaris",
                "version": POLARIS_IMAGE_DIGEST,
            },
        )
        and _json_equal_type_sensitive(
            _nested(sbom, "metadata", "tools", "components"),
            [
                {
                    "type": "application",
                    "author": "anchore",
                    "name": "syft",
                    "version": "1.46.0",
                }
            ],
        ),
        "IMAGE_EVIDENCE",
        "CycloneDX metadata does not bind the exact image and Syft tool",
    )
    forbidden = {"hadoop", "ranger", "jetty-http"}
    matching_components: list[str] = []
    component_references: set[str] = set()
    component_type_counts = {
        "file": 0,
        "library": 0,
        "operating-system": 0,
    }
    for component in components:
        _expect(
            isinstance(component, Mapping),
            "IMAGE_EVIDENCE",
            "CycloneDX component must be an object",
        )
        component_type = component.get("type")
        component_name = component.get("name")
        component_reference = component.get("bom-ref")
        _expect(
            isinstance(component_type, str)
            and component_type in {"library", "operating-system", "file"}
            and isinstance(component_name, str)
            and bool(component_name.strip())
            and isinstance(component_reference, str)
            and bool(component_reference.strip())
            and component_reference not in component_references,
            "IMAGE_EVIDENCE",
            "CycloneDX component identity is incomplete or duplicated",
        )
        component_references.add(component_reference)
        component_type_counts[component_type] += 1
        if component_type == "library":
            _expect(
                isinstance(component.get("version"), str)
                and bool(component["version"].strip())
                and isinstance(component.get("purl"), str)
                and bool(component["purl"].strip())
                and component["purl"].startswith("pkg:"),
                "IMAGE_EVIDENCE",
                "CycloneDX library identity is incomplete",
            )
        elif component_type == "operating-system":
            _expect(
                component_name == "amzn"
                and component.get("version") == "2023",
                "IMAGE_EVIDENCE",
                "CycloneDX operating-system identity is incomplete",
            )
        else:
            _expect(
                component_name.startswith("/"),
                "IMAGE_EVIDENCE",
                "CycloneDX file identity is not an absolute image path",
            )
            hashes = component.get("hashes")
            hash_values = (
                {
                    item["alg"]: item["content"]
                    for item in hashes
                    if isinstance(item, Mapping)
                    and isinstance(item.get("alg"), str)
                    and isinstance(item.get("content"), str)
                }
                if isinstance(hashes, list)
                else {}
            )
            _expect(
                isinstance(hashes, list)
                and len(hashes) == 2
                and len(hash_values) == 2
                and set(hash_values) == {"SHA-1", "SHA-256"}
                and re.fullmatch(r"[0-9a-f]{40}", hash_values["SHA-1"])
                is not None
                and re.fullmatch(r"[0-9a-f]{64}", hash_values["SHA-256"])
                is not None,
                "IMAGE_EVIDENCE",
                "CycloneDX file hashes are incomplete",
            )
        identity = " ".join(
            str(component.get(field, ""))
            for field in ("group", "name", "purl", "bom-ref")
        ).lower()
        if any(term in identity for term in forbidden):
            matching_components.append(identity)
    _expect(
        component_type_counts
        == {
            "file": 6_136,
            "library": 594,
            "operating-system": 1,
        },
        "IMAGE_EVIDENCE",
        "CycloneDX component type distribution changed",
    )
    _expect(
        not matching_components,
        "IMAGE_EVIDENCE",
        "SBOM contains forbidden Hadoop, Ranger, or Jetty runtime components",
    )
    sbom_policy = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "sbom-policy.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        sbom_policy
        == {
            "forbidden_component_terms": [
                "hadoop",
                "ranger",
                "jetty-http",
            ],
            "matching_components": [],
            "result": "passed",
            "schema_version": 1,
        },
        "IMAGE_EVIDENCE",
        "retained SBOM policy result changed",
    )

    trivy = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "trivy.json",
        "IMAGE_EVIDENCE",
    )
    results = trivy.get("Results") if isinstance(trivy, Mapping) else None
    _expect(
        isinstance(trivy, Mapping)
        and trivy.get("SchemaVersion") == 2
        and trivy.get("ArtifactName") == POLARIS_IMAGE_REFERENCE
        and trivy.get("ArtifactType") == "container_image"
        and isinstance(results, list)
        and [
            (
                result.get("Target"),
                result.get("Class"),
                result.get("Type"),
                (
                    len(result["Packages"])
                    if isinstance(result.get("Packages"), list)
                    else None
                ),
            )
            for result in results
            if isinstance(result, Mapping)
        ]
        == [
            (
                (
                    f"{POLARIS_IMAGE_REFERENCE} "
                    "(amazon 2023.12.20260710 (Amazon Linux))"
                ),
                "os-pkgs",
                "amazon",
                133,
            ),
            ("Java", "lang-pkgs", "jar", 456),
        ]
        and all(
            isinstance(result, Mapping)
            and isinstance(result.get("Packages"), list)
            and bool(result["Packages"])
            and (
                "Vulnerabilities" not in result
                or isinstance(result.get("Vulnerabilities"), list)
            )
            for result in results
        ),
        "IMAGE_EVIDENCE",
        "retained Trivy report does not bind the exact image and scan scopes",
    )
    retained_config = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "image-config.json",
        "IMAGE_EVIDENCE",
    )
    metadata = trivy.get("Metadata")
    diff_ids = _nested(retained_config, "rootfs", "diff_ids")
    trivy_layers = metadata.get("Layers") if isinstance(metadata, Mapping) else None
    _expect(
        isinstance(retained_config, Mapping)
        and isinstance(diff_ids, list)
        and isinstance(metadata, Mapping)
        and metadata.get("Reference") == POLARIS_IMAGE_REFERENCE
        and metadata.get("RepoDigests") == [POLARIS_IMAGE_REFERENCE]
        and metadata.get("ImageID")
        == (
            "sha256:"
            + release["evidence"]["records"]["image-config.json"]["sha256"]
        )
        and metadata.get("DiffIDs") == diff_ids
        and metadata.get("OS")
        == {
            "Family": "amazon",
            "Name": "2023.12.20260710 (Amazon Linux)",
        }
        and _nested(metadata, "ImageConfig", "architecture")
        == retained_config.get("architecture")
        and _nested(metadata, "ImageConfig", "os") == retained_config.get("os")
        and _nested(metadata, "ImageConfig", "created")
        == retained_config.get("created")
        and _json_equal_type_sensitive(
            _nested(metadata, "ImageConfig", "rootfs"),
            retained_config.get("rootfs"),
        )
        and _json_equal_type_sensitive(
            _nested(metadata, "ImageConfig", "config"),
            retained_config.get("config"),
        )
        and isinstance(trivy_layers, list)
        and len(trivy_layers) == len(diff_ids)
        and all(
            isinstance(layer, Mapping)
            and type(layer.get("Size")) is int
            and layer["Size"] > 0
            and layer.get("DiffID") == diff_id
            for layer, diff_id in zip(trivy_layers, diff_ids)
        ),
        "IMAGE_EVIDENCE",
        "retained Trivy metadata does not bind the exact OCI config and layers",
    )
    severities: list[str] = []
    recognized_severities = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
    for result in results:
        assert isinstance(result, Mapping)
        packages = result["Packages"]
        _expect(
            all(
                isinstance(package, Mapping)
                and isinstance(package.get("Name"), str)
                and bool(package["Name"].strip())
                and isinstance(package.get("Version"), str)
                and bool(package["Version"].strip())
                and isinstance(package.get("Identifier"), Mapping)
                and isinstance(package["Identifier"].get("PURL"), str)
                and bool(package["Identifier"]["PURL"].strip())
                and isinstance(package["Identifier"].get("UID"), str)
                and bool(package["Identifier"]["UID"].strip())
                for package in packages
            ),
            "IMAGE_EVIDENCE",
            "retained Trivy package inventory contains incomplete identities",
        )
        _expect(
            len(
                {
                    package["Identifier"]["UID"]
                    for package in packages
                }
            )
            == len(packages),
            "IMAGE_EVIDENCE",
            "retained Trivy package inventory contains duplicate identities",
        )
        if result["Class"] == "os-pkgs":
            _expect(
                len(
                    {
                        package["Identifier"]["PURL"]
                        for package in packages
                    }
                )
                == len(packages),
                "IMAGE_EVIDENCE",
                "retained Trivy OS package inventory contains duplicate PURLs",
            )
        else:
            _expect(
                all(
                    isinstance(package.get("FilePath"), str)
                    and bool(package["FilePath"].strip())
                    for package in packages
                )
                and len(
                    {
                        (
                            package["Identifier"]["PURL"],
                            package["FilePath"],
                        )
                        for package in packages
                    }
                )
                == len(packages),
                "IMAGE_EVIDENCE",
                "retained Trivy Java package inventory contains duplicate paths",
            )
        vulnerabilities = result.get("Vulnerabilities", [])
        _expect(
            isinstance(vulnerabilities, list)
            and all(
                isinstance(vulnerability, Mapping)
                and isinstance(vulnerability.get("VulnerabilityID"), str)
                and bool(vulnerability["VulnerabilityID"].strip())
                and isinstance(vulnerability.get("PkgName"), str)
                and bool(vulnerability["PkgName"].strip())
                and isinstance(vulnerability.get("InstalledVersion"), str)
                and bool(vulnerability["InstalledVersion"].strip())
                and isinstance(vulnerability.get("Severity"), str)
                and vulnerability["Severity"].upper() in recognized_severities
                for vulnerability in vulnerabilities
            ),
            "IMAGE_EVIDENCE",
            "retained Trivy vulnerability records are malformed",
        )
        severities.extend(
            vulnerability["Severity"].upper()
            for vulnerability in vulnerabilities
        )
    _expect(
        severities.count("HIGH") == 0
        and severities.count("CRITICAL") == 0
        and release.get("vulnerabilities") == {"high": 0, "critical": 0},
        "IMAGE_EVIDENCE",
        "retained Trivy report exceeds the zero High/Critical gate",
    )
    trivy_version = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "trivy-version.json",
        "IMAGE_EVIDENCE",
    )
    toolchain = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "toolchain.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        isinstance(trivy_version, Mapping)
        and trivy_version.get("Version") == "0.72.0"
        and isinstance(toolchain, Mapping)
        and toolchain.get("trivy_version") == "v0.72.0"
        and toolchain.get("syft_version") == "v1.46.0"
        and toolchain.get("runner")
        == {
            "architecture": "aarch64",
            "label": "ubuntu-24.04-arm",
            "os": "Linux",
        },
        "IMAGE_EVIDENCE",
        "retained scanner, SBOM, or runner toolchain changed",
    )


def _audit_image_sigstore_structure(
    root: Path,
) -> Mapping[str, Any]:
    signature_bundle = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "cosign-signature-bundle.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        isinstance(signature_bundle, Mapping),
        "IMAGE_EVIDENCE",
        "retained image signature bundle must be an object",
    )
    signature_statement = _decode_image_dsse_statement(
        signature_bundle,
        "cosign-signature-bundle.json",
    )
    _expect_image_statement(
        signature_statement,
        "https://sigstore.dev/cosign/sign/v1",
        "cosign-signature-bundle.json",
    )
    registry_bundles = _load_jsonl(
        root,
        POLARIS_IMAGE_EVIDENCE / "registry-signature-bundles.jsonl",
        "IMAGE_EVIDENCE",
    )
    _expect(
        registry_bundles == [signature_bundle],
        "IMAGE_EVIDENCE",
        "registry signature download differs from the retained detached bundle",
    )
    verification = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "cosign-verify.json",
        "IMAGE_EVIDENCE",
    )
    _expect_fields(
        verification,
        {
            ("schema_version",): 1,
            ("reference",): POLARIS_IMAGE_REFERENCE,
            ("detached_bundle_verified",): True,
            ("registry_signature_verified",): True,
            (
                "certificate_constraints",
                "issuer",
            ): POLARIS_IMAGE_PUBLISHER_ISSUER,
            (
                "certificate_constraints",
                "identity",
            ): POLARIS_IMAGE_PUBLISHER_IDENTITY,
            (
                "certificate_constraints",
                "github_workflow_repository",
            ): POLARIS_IMAGE_PUBLISHER_REPOSITORY,
            (
                "certificate_constraints",
                "github_workflow_ref",
            ): POLARIS_IMAGE_PUBLISHER_REF,
            (
                "certificate_constraints",
                "github_workflow_sha",
            ): POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
            (
                "certificate_constraints",
                "github_workflow_trigger",
            ): POLARIS_IMAGE_PUBLISHER_TRIGGER,
            (
                "registry_bundle",
                "exact_matches",
            ): 1,
            (
                "registry_bundle",
                "bundle_sha256",
            ): "f2d75f0812df81eb62dfc5c8ebd8fbe4b38da7cb003a85643a6aebf289c9a280",
        },
        "IMAGE_EVIDENCE",
    )
    verified_payloads = verification.get("verified_payloads")
    _expect(
        isinstance(verified_payloads, list)
        and len(verified_payloads) == 1
        and _nested(
            verified_payloads[0],
            "critical",
            "identity",
            "docker-reference",
        )
        == POLARIS_IMAGE_REFERENCE
        and _nested(
            verified_payloads[0],
            "critical",
            "image",
            "docker-manifest-digest",
        )
        == POLARIS_IMAGE_DIGEST
        and _nested(verified_payloads[0], "critical", "type")
        == "https://sigstore.dev/cosign/sign/v1",
        "IMAGE_EVIDENCE",
        "retained registry verification payload changed",
    )

    for bundle_name, predicate_name, predicate_type in (
        (
            "sbom-attestation-bundle.json",
            "polaris-1.6.0-arm64.cdx.json",
            "https://cyclonedx.org/bom",
        ),
        (
            "trivy-attestation-bundle.json",
            "trivy.json",
            "https://shirokuma.dev/attestations/trivy/v1",
        ),
    ):
        bundle = _load_json_value(
            root,
            POLARIS_IMAGE_EVIDENCE / bundle_name,
            "IMAGE_EVIDENCE",
        )
        _expect(
            isinstance(bundle, Mapping),
            "IMAGE_EVIDENCE",
            f"{bundle_name} must be an object",
        )
        statement = _decode_image_dsse_statement(bundle, bundle_name)
        _expect_image_statement(statement, predicate_type, bundle_name)
        predicate = _load_json_value(
            root,
            POLARIS_IMAGE_EVIDENCE / predicate_name,
            "IMAGE_EVIDENCE",
        )
        _expect(
            statement.get("predicate") == predicate,
            "IMAGE_EVIDENCE",
            f"{bundle_name} does not sign the retained {predicate_name}",
        )

    slsa = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "slsa-verify.json",
        "IMAGE_EVIDENCE",
    )
    promotion_slsa = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "promotion-slsa-verify.json",
        "IMAGE_EVIDENCE",
    )
    _expect(
        isinstance(slsa, list)
        and len(slsa) == 1
        and isinstance(slsa[0], Mapping)
        and isinstance(promotion_slsa, list)
        and len(promotion_slsa) == 1
        and isinstance(promotion_slsa[0], Mapping),
        "IMAGE_EVIDENCE",
        "SLSA verification must contain exactly one result",
    )
    nested_bundle = _nested(slsa[0], "attestation", "bundle")
    promotion_bundle = _nested(promotion_slsa[0], "attestation", "bundle")
    _expect(
        isinstance(nested_bundle, Mapping)
        and nested_bundle == promotion_bundle,
        "IMAGE_EVIDENCE",
        "promotion did not reverify the exact retained SLSA bundle",
    )
    slsa_bundles = _load_jsonl(
        root,
        POLARIS_IMAGE_EVIDENCE / "slsa-bundles.jsonl",
        "IMAGE_EVIDENCE",
    )
    _expect(
        slsa_bundles == [nested_bundle],
        "IMAGE_EVIDENCE",
        "registry SLSA download differs from the verified retained bundle",
    )
    slsa_statement = _decode_image_dsse_statement(
        nested_bundle,
        "slsa-bundles.jsonl",
    )
    _expect_image_statement(
        slsa_statement,
        "https://slsa.dev/provenance/v1",
        "slsa-bundles.jsonl",
    )
    _expect(
        _nested(
            slsa_statement,
            "predicate",
            "buildDefinition",
            "buildType",
        )
        == "https://actions.github.io/buildtypes/workflow/v1"
        and _json_equal_type_sensitive(
            _nested(
                slsa_statement,
                "predicate",
                "buildDefinition",
                "internalParameters",
                "github",
            ),
            {
                "event_name": "push",
                "repository_id": "1289807958",
                "repository_owner_id": "257892020",
                "runner_environment": "github-hosted",
            },
        )
        and _nested(
            slsa_statement,
            "predicate",
            "runDetails",
            "builder",
            "id",
        )
        == POLARIS_IMAGE_PUBLISHER_IDENTITY
        and _nested(
            slsa_statement,
            "predicate",
            "buildDefinition",
            "externalParameters",
            "workflow",
        )
        == {
            "path": POLARIS_IMAGE_WORKFLOW.as_posix(),
            "ref": POLARIS_IMAGE_PUBLISHER_REF,
            "repository": "https://github.com/TommyKammy/Shirokuma",
        }
        and _nested(
            slsa_statement,
            "predicate",
            "buildDefinition",
            "resolvedDependencies",
        )
        == [
            {
                "uri": (
                    "git+https://github.com/TommyKammy/Shirokuma"
                    "@refs/heads/main"
                ),
                "digest": {
                    "gitCommit": POLARIS_IMAGE_PUBLISHER_SOURCE_SHA
                },
            }
        ]
        and _nested(
            slsa_statement,
            "predicate",
            "runDetails",
            "metadata",
            "invocationId",
        )
        == (
            "https://github.com/TommyKammy/Shirokuma/actions/runs/"
            f"{POLARIS_IMAGE_PUBLISHER_RUN_ID}/attempts/"
            f"{POLARIS_IMAGE_PUBLISHER_RUN_ATTEMPT}"
        ),
        "IMAGE_EVIDENCE",
        "SLSA provenance does not bind the exact workflow run and commit",
    )
    _expect(
        _nested(slsa[0], "verificationResult", "statement")
        == slsa_statement
        and _nested(promotion_slsa[0], "verificationResult", "statement")
        == slsa_statement,
        "IMAGE_EVIDENCE",
        "SLSA verification result differs from its signed statement",
    )
    promotion_cosign = _load_json_value(
        root,
        POLARIS_IMAGE_EVIDENCE / "promotion-cosign-verify.json",
        "IMAGE_EVIDENCE",
    )
    expected_types = {
        "https://sigstore.dev/cosign/sign/v1",
        "https://slsa.dev/provenance/v1",
        "https://cyclonedx.org/bom",
        "https://shirokuma.dev/attestations/trivy/v1",
    }
    _expect(
        isinstance(promotion_cosign, list)
        and len(promotion_cosign) == 4
        and {
            _nested(item, "critical", "type")
            for item in promotion_cosign
            if isinstance(item, Mapping)
        }
        == expected_types
        and all(
            _nested(
                item,
                "critical",
                "identity",
                "docker-reference",
            )
            == POLARIS_IMAGE_REFERENCE
            and _nested(
                item,
                "critical",
                "image",
                "docker-manifest-digest",
            )
            == POLARIS_IMAGE_DIGEST
            for item in promotion_cosign
            if isinstance(item, Mapping)
        ),
        "IMAGE_EVIDENCE",
        "promotion verification does not bind every required predicate",
    )
    return nested_bundle


def _reverify_image_sigstore_cryptographically(
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
        _fail("IMAGE_EVIDENCE", f"cannot inspect Cosign: {error}")
    _expect(
        version.returncode == 0
        and re.search(r"(?m)^GitVersion:\s+v3\.1\.1\s*$", version.stdout)
        is not None,
        "IMAGE_EVIDENCE",
        "Cosign 3.1.1 is required for retained image evidence",
    )
    common = [
        "--certificate-identity",
        POLARIS_IMAGE_PUBLISHER_IDENTITY,
        "--certificate-oidc-issuer",
        POLARIS_IMAGE_PUBLISHER_ISSUER,
        "--certificate-github-workflow-repository",
        POLARIS_IMAGE_PUBLISHER_REPOSITORY,
        "--certificate-github-workflow-ref",
        POLARIS_IMAGE_PUBLISHER_REF,
        "--certificate-github-workflow-sha",
        POLARIS_IMAGE_PUBLISHER_SOURCE_SHA,
        "--certificate-github-workflow-trigger",
        POLARIS_IMAGE_PUBLISHER_TRIGGER,
    ]
    manifest = POLARIS_IMAGE_EVIDENCE / "image-manifest.json"
    _run_cosign(
        root,
        [
            "verify-blob",
            "--bundle",
            (
                POLARIS_IMAGE_EVIDENCE
                / "cosign-signature-bundle.json"
            ).as_posix(),
            *common,
            manifest.as_posix(),
        ],
        "retained image signature-bundle verification",
        code="IMAGE_EVIDENCE",
    )
    _run_cosign(
        root,
        ["verify", *common, POLARIS_IMAGE_REFERENCE],
        "authoritative registry image verification",
        code="IMAGE_EVIDENCE",
    )
    for bundle_name, cli_type in (
        ("sbom-attestation-bundle.json", "cyclonedx"),
        (
            "trivy-attestation-bundle.json",
            "https://shirokuma.dev/attestations/trivy/v1",
        ),
    ):
        _run_cosign(
            root,
            [
                "verify-blob-attestation",
                "--bundle",
                (POLARIS_IMAGE_EVIDENCE / bundle_name).as_posix(),
                "--type",
                cli_type,
                *common,
                manifest.as_posix(),
            ],
            f"retained {bundle_name} verification",
            code="IMAGE_EVIDENCE",
        )
    with tempfile.TemporaryDirectory(
        prefix="polaris-image-slsa-bundle-"
    ) as directory:
        bundle_path = Path(directory) / "bundle.json"
        try:
            bundle_path.write_text(
                json.dumps(slsa_bundle, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError as error:
            _fail(
                "IMAGE_EVIDENCE",
                f"cannot stage retained image SLSA bundle: {error}",
            )
        _run_cosign(
            root,
            [
                "verify-blob-attestation",
                "--bundle",
                bundle_path.as_posix(),
                "--type",
                "slsaprovenance1",
                *common,
                manifest.as_posix(),
            ],
            "retained image SLSA-bundle verification",
            code="IMAGE_EVIDENCE",
        )


def _audit_image_publication_evidence(
    root: Path,
    image_crypto_verifier: ImageCryptoVerifier,
) -> Mapping[str, Any]:
    release = _audit_image_release_record(root)
    _audit_image_evidence_inventory(root, release)
    _audit_image_manifest_and_runtime(root, release)
    _audit_image_build_and_scan(root, release)
    slsa_bundle = _audit_image_sigstore_structure(root)
    image_crypto_verifier(root, slsa_bundle)
    return release


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
                "atomic_admission_receipt",
                "resident_ledger",
                "runtime_manifests",
                "next_action",
            },
            ("candidate",): {
                "index_reference",
                "arm64_reference",
                "attestation_manifest_digest",
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
                "sbom_vulnerability_db_updated_at",
                "scanned_library_components",
                "high",
                "critical",
                "authoritative_for_atomic_admission",
            },
            ("evidence_contract",): {
                "self_manifest",
                "paths",
                "publisher",
                "signatures",
                "provenance",
                "upstream_sbom",
                "independent_sbom",
                "vulnerability_scan",
                "cryptographic_reverification",
            },
            ("evidence_contract", "self_manifest"): {
                "path",
                "sha256",
                "size",
            },
            ("evidence_contract", "paths"): {
                "index_manifest",
                "arm64_manifest",
                "attestation_manifest",
                "index_signature_payload",
                "index_signature_bundle",
                "arm64_signature_payload",
                "arm64_signature_bundle",
                "slsa_envelope",
                "slsa_bundle",
                "spdx_envelope",
                "spdx_bundle",
                "trusted_root",
                "cyclonedx_sbom",
                "trivy_report",
                "trivy_version",
                "trivy_sbom_report",
                "trivy_sbom_version",
                "verification",
            },
            ("evidence_contract", "publisher"): {
                "issuer",
                "identity",
                "workflow_name",
                "workflow_repository",
                "workflow_ref",
                "workflow_trigger",
            },
            ("evidence_contract", "signatures"): {"index", "arm64"},
            ("evidence_contract", "signatures", "index"): {
                "workflow_commit",
                "transparency_log_index",
            },
            ("evidence_contract", "signatures", "arm64"): {
                "workflow_commit",
                "transparency_log_index",
            },
            ("evidence_contract", "provenance"): {
                "predicate_type",
                "subject_reference",
                "builder",
                "build_type",
                "invocation_id",
                "workflow_commit",
                "transparency_log_index",
            },
            ("evidence_contract", "upstream_sbom"): {
                "predicate_type",
                "subject_reference",
                "spdx_version",
                "package_count",
                "workflow_commit",
                "transparency_log_index",
            },
            ("evidence_contract", "independent_sbom"): {
                "format",
                "generator",
                "component_count",
            },
            ("evidence_contract", "vulnerability_scan"): {
                "scanner",
                "severity",
                "scan_scopes",
                "library_component_count",
                "covered_library_component_count",
                "maximum_high",
                "maximum_critical",
                "image_database_updated_at",
                "sbom_database_updated_at",
                "rescan_required_at_atomic_admission",
                "maximum_age_hours_at_atomic_admission",
            },
            ("evidence_contract", "cryptographic_reverification"): {
                "cosign",
                "trusted_root_media_type",
                "offline_retained_bundle_verification",
            },
            ("atomic_admission_receipt",): {"path", "sha256"},
            ("resident_ledger",): {"permitted", "atomic_with"},
            ("runtime_manifests",): {"permitted"},
        },
        "POSTGRES_ADMISSION",
    )
    _expect_fields(
        admission,
        {
            ("schema_version",): 3,
            ("component",): "postgresql",
            ("version",): "18.4",
            ("platform",): "linux/arm64",
            ("admission",): "approved",
            ("state",): "runtime_acceptance_pending",
            (
                "source",
            ): "https://github.com/chainguard-images/images/tree/main/images/postgres",
            ("candidate", "index_reference"): POSTGRES_INDEX,
            ("candidate", "arm64_reference"): POSTGRES_ARM64,
            (
                "candidate",
                "attestation_manifest_digest",
            ): POSTGRES_ATTESTATION,
            ("candidate", "availability_preflight_required"): True,
            ("observation", "observed_at"): "2026-07-20",
            (
                "observation",
                "signature",
            ): "retained-and-offline-reverified",
            (
                "observation",
                "arm64_index_membership",
            ): "retained-and-reverified",
            (
                "observation",
                "slsa_provenance",
            ): "retained-and-offline-reverified",
            (
                "observation",
                "upstream_spdx",
            ): "retained-and-offline-reverified",
            ("observation", "upstream_spdx_package_count"): 257,
            ("observation", "trivy_version"): "0.72.0",
            (
                "observation",
                "vulnerability_db_updated_at",
            ): "2026-07-20T01:09:07.303099965Z",
            (
                "observation",
                "sbom_vulnerability_db_updated_at",
            ): "2026-07-19T18:43:16.060990559Z",
            ("observation", "scanned_library_components"): 60,
            (
                "observation",
                "authoritative_for_atomic_admission",
            ): False,
            ("observation", "high"): 0,
            ("observation", "critical"): 0,
            (
                "evidence_contract",
                "self_manifest",
                "path",
            ): "bootstrap/postgresql/v18.4/evidence/evidence.sha256",
            (
                "evidence_contract",
                "self_manifest",
                "sha256",
            ): POSTGRES_EVIDENCE_MANIFEST_SHA256,
            (
                "evidence_contract",
                "self_manifest",
                "size",
            ): POSTGRES_EVIDENCE_MANIFEST_SIZE,
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
                "attestation_manifest",
            ): "bootstrap/postgresql/v18.4/evidence/attestation-manifest.json",
            (
                "evidence_contract",
                "paths",
                "index_signature_payload",
            ): "bootstrap/postgresql/v18.4/evidence/index-signature-payload.json",
            (
                "evidence_contract",
                "paths",
                "index_signature_bundle",
            ): "bootstrap/postgresql/v18.4/evidence/index-signature.sigstore.json",
            (
                "evidence_contract",
                "paths",
                "arm64_signature_payload",
            ): "bootstrap/postgresql/v18.4/evidence/arm64-signature-payload.json",
            (
                "evidence_contract",
                "paths",
                "arm64_signature_bundle",
            ): "bootstrap/postgresql/v18.4/evidence/arm64-signature.sigstore.json",
            (
                "evidence_contract",
                "paths",
                "slsa_envelope",
            ): "bootstrap/postgresql/v18.4/evidence/slsa-attestation-envelope.json",
            (
                "evidence_contract",
                "paths",
                "slsa_bundle",
            ): "bootstrap/postgresql/v18.4/evidence/slsa-provenance.sigstore.json",
            (
                "evidence_contract",
                "paths",
                "spdx_envelope",
            ): "bootstrap/postgresql/v18.4/evidence/spdx-attestation-envelope.json",
            (
                "evidence_contract",
                "paths",
                "spdx_bundle",
            ): "bootstrap/postgresql/v18.4/evidence/spdx-sbom.sigstore.json",
            (
                "evidence_contract",
                "paths",
                "trusted_root",
            ): "bootstrap/postgresql/v18.4/evidence/trusted-root.json",
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
                "trivy_version",
            ): "bootstrap/postgresql/v18.4/evidence/trivy-version.json",
            (
                "evidence_contract",
                "paths",
                "trivy_sbom_report",
            ): "bootstrap/postgresql/v18.4/evidence/trivy-sbom.json",
            (
                "evidence_contract",
                "paths",
                "trivy_sbom_version",
            ): "bootstrap/postgresql/v18.4/evidence/trivy-sbom-version.json",
            (
                "evidence_contract",
                "paths",
                "verification",
            ): "bootstrap/postgresql/v18.4/evidence/cryptographic-verification.json",
            (
                "evidence_contract",
                "publisher",
                "issuer",
            ): POSTGRES_PUBLISHER_ISSUER,
            (
                "evidence_contract",
                "publisher",
                "identity",
            ): POSTGRES_PUBLISHER_IDENTITY,
            (
                "evidence_contract",
                "publisher",
                "workflow_name",
            ): POSTGRES_PUBLISHER_WORKFLOW_NAME,
            (
                "evidence_contract",
                "publisher",
                "workflow_repository",
            ): POSTGRES_PUBLISHER_REPOSITORY,
            (
                "evidence_contract",
                "publisher",
                "workflow_ref",
            ): POSTGRES_PUBLISHER_REF,
            (
                "evidence_contract",
                "publisher",
                "workflow_trigger",
            ): POSTGRES_PUBLISHER_TRIGGER,
            (
                "evidence_contract",
                "signatures",
                "index",
                "workflow_commit",
            ): POSTGRES_RELEASE_WORKFLOW_SHA,
            (
                "evidence_contract",
                "signatures",
                "index",
                "transparency_log_index",
            ): 2_181_460_214,
            (
                "evidence_contract",
                "signatures",
                "arm64",
                "workflow_commit",
            ): POSTGRES_RELEASE_WORKFLOW_SHA,
            (
                "evidence_contract",
                "signatures",
                "arm64",
                "transparency_log_index",
            ): 2_181_460_469,
            (
                "evidence_contract",
                "provenance",
                "predicate_type",
            ): POSTGRES_SLSA_PREDICATE,
            (
                "evidence_contract",
                "provenance",
                "subject_reference",
            ): POSTGRES_ARM64,
            (
                "evidence_contract",
                "provenance",
                "builder",
            ): POSTGRES_SLSA_BUILDER,
            (
                "evidence_contract",
                "provenance",
                "build_type",
            ): POSTGRES_SLSA_BUILD_TYPE,
            (
                "evidence_contract",
                "provenance",
                "invocation_id",
            ): POSTGRES_INDEX,
            (
                "evidence_contract",
                "provenance",
                "workflow_commit",
            ): POSTGRES_SLSA_WORKFLOW_SHA,
            (
                "evidence_contract",
                "provenance",
                "transparency_log_index",
            ): 2_177_108_005,
            (
                "evidence_contract",
                "upstream_sbom",
                "predicate_type",
            ): POSTGRES_SPDX_PREDICATE,
            (
                "evidence_contract",
                "upstream_sbom",
                "subject_reference",
            ): POSTGRES_ARM64,
            (
                "evidence_contract",
                "upstream_sbom",
                "spdx_version",
            ): "SPDX-2.3",
            (
                "evidence_contract",
                "upstream_sbom",
                "package_count",
            ): 257,
            (
                "evidence_contract",
                "upstream_sbom",
                "workflow_commit",
            ): POSTGRES_RELEASE_WORKFLOW_SHA,
            (
                "evidence_contract",
                "upstream_sbom",
                "transparency_log_index",
            ): 2_181_460_833,
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
                "independent_sbom",
                "component_count",
            ): 4_725,
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
                "scan_scopes",
            ): [
                "image:os-pkgs/wolfi",
                "sbom:os-pkgs/wolfi",
                "sbom:lang-pkgs/gobinary",
            ],
            (
                "evidence_contract",
                "vulnerability_scan",
                "library_component_count",
            ): 60,
            (
                "evidence_contract",
                "vulnerability_scan",
                "covered_library_component_count",
            ): 60,
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
                "image_database_updated_at",
            ): "2026-07-20T01:09:07.303099965Z",
            (
                "evidence_contract",
                "vulnerability_scan",
                "sbom_database_updated_at",
            ): "2026-07-19T18:43:16.060990559Z",
            (
                "evidence_contract",
                "vulnerability_scan",
                "rescan_required_at_atomic_admission",
            ): True,
            (
                "evidence_contract",
                "vulnerability_scan",
                "maximum_age_hours_at_atomic_admission",
            ): 24,
            (
                "evidence_contract",
                "cryptographic_reverification",
                "cosign",
            ): "3.1.1",
            (
                "evidence_contract",
                "cryptographic_reverification",
                "trusted_root_media_type",
            ): POSTGRES_TRUSTED_ROOT_MEDIA_TYPE,
            (
                "evidence_contract",
                "cryptographic_reverification",
                "offline_retained_bundle_verification",
            ): True,
            (
                "atomic_admission_receipt",
                "path",
            ): POLARIS_ATOMIC_ADMISSION.as_posix(),
            (
                "atomic_admission_receipt",
                "sha256",
            ): POLARIS_ATOMIC_ADMISSION_SHA256,
            ("resident_ledger", "permitted"): True,
            ("resident_ledger", "atomic_with"): "polaris",
            ("runtime_manifests", "permitted"): False,
            (
                "next_action",
            ): "complete-runtime-acceptance",
        },
        "POSTGRES_ADMISSION",
    )
    return admission


PostgresCryptoVerifier = Callable[[Path], None]


def _audit_postgres_evidence_inventory(
    root: Path,
    admission: Mapping[str, Any],
) -> None:
    code = "POSTGRES_EVIDENCE"
    directory = root / POSTGRES_EVIDENCE
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        code,
        "retained PostgreSQL evidence directory is invalid",
    )
    actual_names: set[str] = set()
    for path in directory.iterdir():
        _expect(
            path.is_file() and not path.is_symlink(),
            code,
            f"retained PostgreSQL evidence must be a regular file: {path.name}",
        )
        actual_names.add(path.name)
    _expect(
        actual_names
        == POSTGRES_EVIDENCE_REQUIRED | {"README.md", "evidence.sha256"},
        code,
        "retained PostgreSQL evidence inventory must be closed",
    )

    manifest_relative = POSTGRES_EVIDENCE / "evidence.sha256"
    manifest_hash, manifest_size = _sha256_and_size(
        root,
        manifest_relative,
        code,
    )
    _expect(
        (manifest_hash, manifest_size)
        == (
            POSTGRES_EVIDENCE_MANIFEST_SHA256,
            POSTGRES_EVIDENCE_MANIFEST_SIZE,
        )
        == (
            _nested(
                admission,
                "evidence_contract",
                "self_manifest",
                "sha256",
            ),
            _nested(
                admission,
                "evidence_contract",
                "self_manifest",
                "size",
            ),
        ),
        code,
        "PostgreSQL self-manifest differs from the reviewed evidence",
    )
    try:
        lines = (root / manifest_relative).read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as error:
        _fail(code, f"cannot read PostgreSQL self-manifest: {error}")
    parsed: dict[str, str] = {}
    ordered_names: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  \./([A-Za-z0-9._-]+)", line)
        _expect(
            match is not None,
            code,
            "PostgreSQL self-manifest contains a noncanonical record",
        )
        assert match is not None
        name = match.group(2)
        _expect(
            name not in parsed,
            code,
            f"PostgreSQL self-manifest duplicates {name}",
        )
        parsed[name] = match.group(1)
        ordered_names.append(name)
    _expect(
        set(parsed) == POSTGRES_EVIDENCE_REQUIRED
        and ordered_names == sorted(ordered_names),
        code,
        "PostgreSQL self-manifest does not close the exact payload inventory",
    )
    for name, wanted in parsed.items():
        actual, size = _sha256_and_size(
            root,
            POSTGRES_EVIDENCE / name,
            code,
        )
        _expect(
            actual == wanted and size > 0,
            code,
            f"{name} differs from the PostgreSQL self-manifest",
        )


def _decode_postgres_base64(value: Any, purpose: str) -> bytes:
    _expect(
        isinstance(value, str) and bool(value),
        "POSTGRES_EVIDENCE",
        f"{purpose} must be nonempty base64",
    )
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        _fail("POSTGRES_EVIDENCE", f"invalid {purpose}: {error}")


def _audit_postgres_bundle_material(
    bundle: Mapping[str, Any],
    *,
    kind: str,
    log_index: int,
    integrated_time: int,
) -> None:
    code = "POSTGRES_EVIDENCE"
    material = bundle.get("verificationMaterial")
    _expect(
        isinstance(material, Mapping)
        and set(material) == {"certificate", "tlogEntries"},
        code,
        "Sigstore bundle verification material is not closed",
    )
    certificate = material.get("certificate")
    _expect(
        isinstance(certificate, Mapping)
        and set(certificate) == {"rawBytes"},
        code,
        "Sigstore bundle must retain one leaf certificate",
    )
    _expect(
        bool(_decode_postgres_base64(certificate.get("rawBytes"), "certificate")),
        code,
        "Sigstore certificate cannot be empty",
    )
    entries = material.get("tlogEntries")
    _expect(
        isinstance(entries, list)
        and len(entries) == 1
        and isinstance(entries[0], Mapping),
        code,
        "Sigstore bundle must retain exactly one transparency-log entry",
    )
    entry = entries[0]
    _expect(
        entry.get("logIndex") == str(log_index)
        and entry.get("integratedTime") == str(integrated_time)
        and entry.get("kindVersion")
        == {"kind": kind, "version": "0.0.1"},
        code,
        "Sigstore transparency-log role binding changed",
    )
    _decode_postgres_base64(
        entry.get("canonicalizedBody"),
        "Rekor canonicalized body",
    )
    promise = entry.get("inclusionPromise")
    proof = entry.get("inclusionProof")
    _expect(
        isinstance(promise, Mapping)
        and set(promise) == {"signedEntryTimestamp"}
        and isinstance(proof, Mapping)
        and set(proof)
        == {"checkpoint", "hashes", "logIndex", "rootHash", "treeSize"},
        code,
        "Sigstore transparency-log proof is incomplete",
    )
    _decode_postgres_base64(
        promise.get("signedEntryTimestamp"),
        "Rekor signed entry timestamp",
    )
    _expect(
        isinstance(proof.get("checkpoint"), Mapping)
        and isinstance(proof["checkpoint"].get("envelope"), str)
        and bool(proof["checkpoint"]["envelope"])
        and isinstance(proof.get("hashes"), list)
        and bool(proof["hashes"]),
        code,
        "Sigstore inclusion proof is incomplete",
    )


def _audit_postgres_signature_bundle(
    root: Path,
    role: str,
    record: Mapping[str, Any],
) -> None:
    code = "POSTGRES_EVIDENCE"
    payload_relative = POSTGRES_EVIDENCE / str(record["payload"])
    bundle_relative = POSTGRES_EVIDENCE / str(record["bundle"])
    payload = _load_json(root, payload_relative, code)
    _expect_keysets(
        payload,
        {
            (): {"critical", "optional"},
            ("critical",): {"identity", "image", "type"},
            ("critical", "identity"): {"docker-reference"},
            ("critical", "image"): {"docker-manifest-digest"},
        },
        code,
    )
    _expect_fields(
        payload,
        {
            (
                "critical",
                "identity",
                "docker-reference",
            ): "cgr.dev/chainguard/postgres",
            (
                "critical",
                "image",
                "docker-manifest-digest",
            ): record["digest"],
            (
                "critical",
                "type",
            ): "cosign container image signature",
            ("optional",): None,
        },
        code,
    )
    bundle = _load_json(root, bundle_relative, code)
    _expect(
        set(bundle)
        == {"mediaType", "messageSignature", "verificationMaterial"}
        and bundle.get("mediaType") == POSTGRES_BUNDLE_MEDIA_TYPE,
        code,
        f"{role} signature must use a standard Sigstore v0.3 bundle",
    )
    signature = bundle.get("messageSignature")
    _expect(
        isinstance(signature, Mapping)
        and set(signature) == {"messageDigest", "signature"}
        and isinstance(signature.get("messageDigest"), Mapping)
        and set(signature["messageDigest"]) == {"algorithm", "digest"}
        and signature["messageDigest"].get("algorithm") == "SHA2_256",
        code,
        f"{role} message signature structure changed",
    )
    try:
        payload_bytes = (root / payload_relative).read_bytes()
    except OSError as error:
        _fail(code, f"cannot read {payload_relative}: {error}")
    wanted_digest = base64.b64encode(
        hashlib.sha256(payload_bytes).digest()
    ).decode("ascii")
    _expect(
        signature["messageDigest"].get("digest") == wanted_digest,
        code,
        f"{role} bundle does not bind the retained signature payload",
    )
    _decode_postgres_base64(
        signature.get("signature"),
        f"{role} signature",
    )
    _audit_postgres_bundle_material(
        bundle,
        kind="hashedrekord",
        log_index=int(record["log_index"]),
        integrated_time=int(record["integrated_time"]),
    )


def _decode_postgres_dsse_statement(
    root: Path,
    role: str,
    descriptor: Mapping[str, Any],
) -> Mapping[str, Any]:
    code = "POSTGRES_EVIDENCE"
    envelope_relative = POSTGRES_EVIDENCE / str(descriptor["envelope"])
    envelope = _load_json(root, envelope_relative, code)
    _expect(
        set(envelope) == {"payloadType", "payload", "signatures"}
        and envelope.get("payloadType") == "application/vnd.in-toto+json",
        code,
        f"{role} DSSE envelope structure changed",
    )
    signatures = envelope.get("signatures")
    _expect(
        isinstance(signatures, list)
        and len(signatures) == 1
        and isinstance(signatures[0], Mapping)
        and set(signatures[0]) == {"keyid", "sig"}
        and signatures[0].get("keyid") == "",
        code,
        f"{role} DSSE envelope must contain one signature",
    )
    _decode_postgres_base64(signatures[0].get("sig"), f"{role} DSSE signature")
    decoded = _decode_postgres_base64(
        envelope.get("payload"),
        f"{role} DSSE payload",
    )
    try:
        statement = json.loads(
            decoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeError, ValueError) as error:
        _fail(code, f"cannot decode {role} DSSE statement: {error}")
    _expect(
        isinstance(statement, Mapping),
        code,
        f"{role} DSSE statement must be an object",
    )

    bundle = _load_json(
        root,
        POSTGRES_EVIDENCE / str(descriptor["bundle"]),
        code,
    )
    _expect(
        set(bundle) == {"mediaType", "dsseEnvelope", "verificationMaterial"}
        and bundle.get("mediaType") == POSTGRES_BUNDLE_MEDIA_TYPE,
        code,
        f"{role} attestation must use a standard Sigstore v0.3 bundle",
    )
    bundled_envelope = bundle.get("dsseEnvelope")
    _expect(
        isinstance(bundled_envelope, Mapping)
        and bundled_envelope.get("payloadType") == envelope["payloadType"]
        and bundled_envelope.get("payload") == envelope["payload"]
        and bundled_envelope.get("signatures")
        == [{"sig": signatures[0]["sig"]}],
        code,
        f"{role} bundle does not bind the retained DSSE envelope",
    )
    _audit_postgres_bundle_material(
        bundle,
        kind="dsse",
        log_index=int(descriptor["log_index"]),
        integrated_time=int(descriptor["integrated_time"]),
    )
    return statement


def _audit_postgres_manifests(
    root: Path,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    code = "POSTGRES_EVIDENCE"
    index_digest, index_size = _sha256_and_size(
        root,
        POSTGRES_EVIDENCE / "index-manifest.json",
        code,
    )
    arm64_digest, arm64_size = _sha256_and_size(
        root,
        POSTGRES_EVIDENCE / "arm64-manifest.json",
        code,
    )
    attestation_digest, attestation_size = _sha256_and_size(
        root,
        POSTGRES_EVIDENCE / "attestation-manifest.json",
        code,
    )
    _expect(
        (
            index_digest,
            index_size,
            arm64_digest,
            arm64_size,
            attestation_digest,
            attestation_size,
        )
        == (
            POSTGRES_INDEX.rsplit("@sha256:", 1)[1],
            1_015,
            POSTGRES_ARM64.rsplit("@sha256:", 1)[1],
            2_510,
            POSTGRES_ATTESTATION.removeprefix("sha256:"),
            29_968,
        ),
        code,
        "retained raw PostgreSQL manifest bytes changed",
    )
    index = _load_json(
        root,
        POSTGRES_EVIDENCE / "index-manifest.json",
        code,
    )
    _expect(
        index.get("schemaVersion") == 2
        and index.get("mediaType")
        == "application/vnd.oci.image.index.v1+json"
        and isinstance(index.get("manifests"), list)
        and len(index["manifests"]) == 2,
        code,
        "PostgreSQL index structure changed",
    )
    descriptors = {
        (
            _nested(item, "platform", "os"),
            _nested(item, "platform", "architecture"),
        ): item
        for item in index["manifests"]
        if isinstance(item, Mapping)
    }
    _expect(
        set(descriptors) == {("linux", "amd64"), ("linux", "arm64")},
        code,
        "PostgreSQL index must contain exactly linux/amd64 and linux/arm64",
    )
    arm64_descriptor = descriptors[("linux", "arm64")]
    _expect(
        arm64_descriptor.get("mediaType")
        == "application/vnd.oci.image.manifest.v1+json"
        and arm64_descriptor.get("digest")
        == POSTGRES_ARM64.rsplit("@", 1)[1]
        and arm64_descriptor.get("size") == arm64_size,
        code,
        "PostgreSQL index does not bind the retained arm64 manifest",
    )
    arm64 = _load_json(
        root,
        POSTGRES_EVIDENCE / "arm64-manifest.json",
        code,
    )
    _expect(
        arm64.get("schemaVersion") == 2
        and arm64.get("mediaType")
        == "application/vnd.oci.image.manifest.v1+json"
        and isinstance(arm64.get("config"), Mapping)
        and isinstance(arm64.get("layers"), list)
        and bool(arm64["layers"]),
        code,
        "retained PostgreSQL arm64 manifest structure changed",
    )

    attestation = _load_json(
        root,
        POSTGRES_EVIDENCE / "attestation-manifest.json",
        code,
    )
    _expect(
        attestation.get("schemaVersion") == 2
        and attestation.get("mediaType")
        == "application/vnd.oci.image.manifest.v1+json"
        and isinstance(attestation.get("layers"), list),
        code,
        "PostgreSQL attestation manifest structure changed",
    )
    statements: dict[str, Mapping[str, Any]] = {}
    for role, wanted in POSTGRES_ATTESTATION_LAYERS.items():
        matches = [
            layer
            for layer in attestation["layers"]
            if isinstance(layer, Mapping)
            and _nested(layer, "annotations", "predicateType")
            == wanted["predicate_type"]
        ]
        _expect(
            len(matches) == 1,
            code,
            f"attestation manifest must contain one {role} layer",
        )
        layer = matches[0]
        _expect(
            layer.get("mediaType")
            == "application/vnd.dsse.envelope.v1+json"
            and layer.get("digest") == wanted["digest"]
            and layer.get("size") == wanted["size"],
            code,
            f"{role} attestation descriptor changed",
        )
        envelope_hash, envelope_size = _sha256_and_size(
            root,
            POSTGRES_EVIDENCE / str(wanted["envelope"]),
            code,
        )
        _expect(
            (
                f"sha256:{envelope_hash}",
                envelope_size,
            )
            == (wanted["digest"], wanted["size"]),
            code,
            f"{role} descriptor does not bind the retained envelope bytes",
        )
        statements[role] = _decode_postgres_dsse_statement(
            root,
            role,
            wanted,
        )
    return statements["slsa"], statements["spdx"]


def _audit_postgres_statements(
    slsa: Mapping[str, Any],
    spdx: Mapping[str, Any],
) -> None:
    code = "POSTGRES_EVIDENCE"
    expected_subject = [
        {
            "name": "cgr.dev/chainguard/postgres",
            "digest": {
                "sha256": POSTGRES_ARM64.rsplit("@sha256:", 1)[1],
            },
        }
    ]
    _expect(
        slsa.get("_type") == "https://in-toto.io/Statement/v0.1"
        and slsa.get("subject") == expected_subject
        and slsa.get("predicateType") == POSTGRES_SLSA_PREDICATE
        and _nested(
            slsa,
            "predicate",
            "runDetails",
            "builder",
            "id",
        )
        == POSTGRES_SLSA_BUILDER
        and _nested(
            slsa,
            "predicate",
            "buildDefinition",
            "buildType",
        )
        == POSTGRES_SLSA_BUILD_TYPE
        and _nested(
            slsa,
            "predicate",
            "runDetails",
            "metadata",
            "invocationId",
        )
        == POSTGRES_INDEX,
        code,
        "retained PostgreSQL SLSA v1 semantics changed",
    )
    packages = _nested(spdx, "predicate", "packages")
    _expect(
        spdx.get("_type") == "https://in-toto.io/Statement/v0.1"
        and spdx.get("subject") == expected_subject
        and spdx.get("predicateType") == POSTGRES_SPDX_PREDICATE
        and _nested(spdx, "predicate", "spdxVersion") == "SPDX-2.3"
        and isinstance(packages, list)
        and len(packages) == 257,
        code,
        "retained PostgreSQL SPDX semantics changed",
    )


def _audit_postgres_sbom_and_scan(
    root: Path,
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
]:
    code = "POSTGRES_EVIDENCE"
    cyclone = _load_json(
        root,
        POSTGRES_EVIDENCE / "postgresql-18.4-arm64.cdx.json",
        code,
    )
    tools = _nested(cyclone, "metadata", "tools", "components")
    root_component = _nested(cyclone, "metadata", "component")
    components = cyclone.get("components")
    _expect(
        cyclone.get("bomFormat") == "CycloneDX"
        and cyclone.get("specVersion") == "1.7"
        and cyclone.get("version") == 1
        and tools
        == [
            {
                "type": "application",
                "author": "anchore",
                "name": "syft",
                "version": "1.46.0",
            }
        ]
        and isinstance(root_component, Mapping)
        and set(root_component) == {"bom-ref", "type", "name", "version"}
        and isinstance(root_component.get("bom-ref"), str)
        and re.fullmatch(r"[0-9a-f]{16}", root_component["bom-ref"])
        is not None
        and root_component.get("type") == "container"
        and root_component.get("name") == "cgr.dev/chainguard/postgres"
        and root_component.get("version")
        == POSTGRES_ARM64.rsplit("@", 1)[1]
        and isinstance(components, list)
        and len(components) == 4_725,
        code,
        "retained PostgreSQL CycloneDX contract changed",
    )
    component_references: set[str] = set()
    component_type_counts = {
        "file": 0,
        "library": 0,
        "operating-system": 0,
    }
    library_identities: set[tuple[str, str]] = set()
    library_components_by_reference: dict[
        str,
        tuple[str, str, str],
    ] = {}
    library_references_by_scan_class: dict[str, set[str]] = {
        "os-pkgs": set(),
        "lang-pkgs": set(),
    }
    for index, component in enumerate(components):
        _expect(
            isinstance(component, Mapping),
            code,
            f"CycloneDX components[{index}] must be an object",
        )
        component_type = component.get("type")
        component_name = component.get("name")
        component_reference = component.get("bom-ref")
        _expect(
            isinstance(component_type, str)
            and component_type in component_type_counts
            and isinstance(component_name, str)
            and bool(component_name.strip())
            and isinstance(component_reference, str)
            and bool(component_reference.strip())
            and component_reference not in component_references,
            code,
            "CycloneDX component identity is incomplete or duplicated",
        )
        component_references.add(component_reference)
        component_type_counts[component_type] += 1
        if component_type == "library":
            component_version = component.get("version")
            component_purl = component.get("purl")
            _expect(
                isinstance(component_version, str)
                and bool(component_version.strip())
                and isinstance(component_purl, str)
                and (
                    component_purl.startswith("pkg:apk/wolfi/")
                    or component_purl.startswith("pkg:golang/")
                ),
                code,
                "CycloneDX library identity is incomplete",
            )
            identity = (component_name, component_version)
            _expect(
                identity not in library_identities,
                code,
                "CycloneDX library identity is duplicated",
            )
            library_identities.add(identity)
            library_components_by_reference[component_reference] = (
                component_name,
                component_version,
                component_purl,
            )
            scan_class = (
                "os-pkgs"
                if component_purl.startswith("pkg:apk/wolfi/")
                else "lang-pkgs"
            )
            library_references_by_scan_class[scan_class].add(
                component_reference
            )
        elif component_type == "operating-system":
            _expect(
                component_name == "wolfi"
                and component.get("version") == "20230201",
                code,
                "CycloneDX operating-system identity changed",
            )
        else:
            hashes = component.get("hashes")
            hash_values = (
                {
                    item["alg"]: item["content"]
                    for item in hashes
                    if isinstance(item, Mapping)
                    and isinstance(item.get("alg"), str)
                    and isinstance(item.get("content"), str)
                }
                if isinstance(hashes, list)
                else {}
            )
            _expect(
                component_name.startswith("/")
                and isinstance(hashes, list)
                and len(hashes) == 2
                and set(hash_values) == {"SHA-1", "SHA-256"}
                and re.fullmatch(r"[0-9a-f]{40}", hash_values["SHA-1"])
                is not None
                and re.fullmatch(r"[0-9a-f]{64}", hash_values["SHA-256"])
                is not None,
                code,
                "CycloneDX file identity or hashes are incomplete",
            )
    _expect(
        component_type_counts
        == {
            "file": 4_664,
            "library": 60,
            "operating-system": 1,
        }
        and ("postgresql-18", "18.4-r6") in library_identities,
        code,
        "CycloneDX component type distribution changed",
    )
    _expect(
        {
            scan_class: len(references)
            for scan_class, references in
            library_references_by_scan_class.items()
        }
        == {"os-pkgs": 56, "lang-pkgs": 4},
        code,
        "CycloneDX library ecosystem distribution changed",
    )
    apk_library_identities = {
        (
            library_components_by_reference[reference][0],
            library_components_by_reference[reference][1],
        )
        for reference in library_references_by_scan_class["os-pkgs"]
    }

    trivy = _load_json(
        root,
        POSTGRES_EVIDENCE / "trivy.json",
        code,
    )
    arm_manifest = _load_json(
        root,
        POSTGRES_EVIDENCE / "arm64-manifest.json",
        code,
    )
    arm_layers = arm_manifest.get("layers")
    metadata = trivy.get("Metadata")
    trivy_layers = (
        metadata.get("Layers") if isinstance(metadata, Mapping) else None
    )
    diff_ids = (
        metadata.get("DiffIDs") if isinstance(metadata, Mapping) else None
    )
    results = trivy.get("Results")
    _expect(
        set(trivy)
        == {
            "SchemaVersion",
            "Trivy",
            "ReportID",
            "CreatedAt",
            "ArtifactID",
            "ArtifactName",
            "ArtifactType",
            "Metadata",
            "Results",
        }
        and trivy.get("SchemaVersion") == 2
        and trivy.get("Trivy") == {"Version": "0.72.0"}
        and trivy.get("ArtifactName") == POSTGRES_ARM64
        and trivy.get("ArtifactType") == "container_image"
        and isinstance(results, list)
        and [
            (
                result.get("Target"),
                result.get("Class"),
                result.get("Type"),
                (
                    len(result["Packages"])
                    if isinstance(result.get("Packages"), list)
                    else None
                ),
            )
            for result in results
            if isinstance(result, Mapping)
        ]
        == [
            (
                f"{POSTGRES_ARM64} (wolfi 20230201)",
                "os-pkgs",
                "wolfi",
                56,
            )
        ],
        code,
        "retained PostgreSQL Trivy report does not bind the exact scan scope",
    )
    _expect(
        isinstance(metadata, Mapping)
        and metadata.get("Reference") == POSTGRES_ARM64
        and metadata.get("RepoDigests") == [POSTGRES_ARM64]
        and metadata.get("ImageID")
        == _nested(arm_manifest, "config", "digest")
        and metadata.get("OS")
        == {"Family": "wolfi", "Name": "20230201"}
        and _nested(metadata, "ImageConfig", "architecture") == "arm64"
        and _nested(metadata, "ImageConfig", "os") == "linux"
        and isinstance(diff_ids, list)
        and _nested(metadata, "ImageConfig", "rootfs", "diff_ids")
        == diff_ids
        and isinstance(arm_layers, list)
        and isinstance(trivy_layers, list)
        and len(arm_layers) == len(diff_ids) == len(trivy_layers)
        and all(
            isinstance(arm_layer, Mapping)
            and isinstance(trivy_layer, Mapping)
            and isinstance(arm_layer.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", arm_layer["digest"])
            is not None
            and isinstance(trivy_layer.get("Digest"), str)
            and isinstance(trivy_layer.get("DiffID"), str)
            and isinstance(diff_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", trivy_layer["Digest"])
            is not None
            and re.fullmatch(r"sha256:[0-9a-f]{64}", trivy_layer["DiffID"])
            is not None
            and re.fullmatch(r"sha256:[0-9a-f]{64}", diff_id)
            is not None
            and trivy_layer.get("Digest") == arm_layer.get("digest")
            and trivy_layer.get("DiffID") == diff_id
            and type(trivy_layer.get("Size")) is int
            and trivy_layer["Size"] > 0
            for arm_layer, trivy_layer, diff_id in zip(
                arm_layers,
                trivy_layers,
                diff_ids,
            )
        ),
        code,
        "retained PostgreSQL Trivy metadata does not bind the OCI image",
    )
    vulnerabilities: list[Mapping[str, Any]] = []
    scanned_package_identities: set[tuple[str, str]] = set()
    scanned_package_uids: set[str] = set()
    scanned_package_purls: set[str] = set()
    valid_layer_pairs = {
        (layer["Digest"], layer["DiffID"])
        for layer in trivy_layers
    }
    for index, result in enumerate(results):
        _expect(
            isinstance(result, Mapping),
            code,
            f"Trivy Results[{index}] must be an object",
        )
        packages = result.get("Packages")
        _expect(
            isinstance(packages, list) and bool(packages),
            code,
            f"Trivy Results[{index}].Packages must be non-empty",
        )
        for package_index, package in enumerate(packages):
            identifier = (
                package.get("Identifier")
                if isinstance(package, Mapping)
                else None
            )
            layer = (
                package.get("Layer")
                if isinstance(package, Mapping)
                else None
            )
            layer_digest = (
                layer.get("Digest") if isinstance(layer, Mapping) else None
            )
            layer_diff_id = (
                layer.get("DiffID") if isinstance(layer, Mapping) else None
            )
            _expect(
                isinstance(package, Mapping)
                and isinstance(package.get("Name"), str)
                and bool(package["Name"].strip())
                and isinstance(package.get("Version"), str)
                and bool(package["Version"].strip())
                and package.get("ID")
                == f"{package['Name']}@{package['Version']}"
                and package.get("Arch") == "aarch64"
                and package.get("AnalyzedBy") == "apk"
                and isinstance(identifier, Mapping)
                and isinstance(identifier.get("PURL"), str)
                and bool(identifier["PURL"].strip())
                and identifier["PURL"].startswith("pkg:apk/wolfi/")
                and isinstance(identifier.get("UID"), str)
                and bool(identifier["UID"].strip())
                and isinstance(layer, Mapping)
                and isinstance(layer_digest, str)
                and isinstance(layer_diff_id, str)
                and (layer_digest, layer_diff_id) in valid_layer_pairs,
                code,
                (
                    f"Trivy Results[{index}].Packages[{package_index}] "
                    "has an incomplete or unbound identity"
                ),
            )
            package_identity = (package["Name"], package["Version"])
            _expect(
                package_identity in apk_library_identities
                and package_identity not in scanned_package_identities
                and identifier["UID"] not in scanned_package_uids
                and identifier["PURL"] not in scanned_package_purls,
                code,
                (
                    "Trivy image package inventory is duplicated or absent "
                    "from the exact APK SBOM partition"
                ),
            )
            scanned_package_identities.add(package_identity)
            scanned_package_uids.add(identifier["UID"])
            scanned_package_purls.add(identifier["PURL"])
        findings = (
            result["Vulnerabilities"]
            if "Vulnerabilities" in result
            else []
        )
        _expect(
            isinstance(findings, list),
            code,
            f"Trivy Results[{index}].Vulnerabilities must be a list",
        )
        for finding in findings:
            _expect(
                isinstance(finding, Mapping)
                and isinstance(finding.get("VulnerabilityID"), str)
                and bool(finding["VulnerabilityID"].strip())
                and isinstance(finding.get("PkgName"), str)
                and bool(finding["PkgName"].strip())
                and isinstance(finding.get("InstalledVersion"), str)
                and bool(finding["InstalledVersion"].strip())
                and isinstance(finding.get("Severity"), str)
                and finding["Severity"] in {"HIGH", "CRITICAL"},
                code,
                f"Trivy Results[{index}] contains a malformed finding",
            )
            vulnerabilities.append(finding)
    _expect(
        scanned_package_identities == apk_library_identities,
        code,
        "Trivy image scan does not close the exact APK library inventory",
    )

    trivy_sbom = _load_json(
        root,
        POSTGRES_EVIDENCE / "trivy-sbom.json",
        code,
    )
    trivy_sbom_results = trivy_sbom.get("Results")
    sbom_path = (
        "bootstrap/postgresql/v18.4/evidence/"
        "postgresql-18.4-arm64.cdx.json"
    )
    _expect(
        set(trivy_sbom)
        == {
            "SchemaVersion",
            "Trivy",
            "ReportID",
            "CreatedAt",
            "ArtifactName",
            "ArtifactType",
            "Metadata",
            "Results",
        }
        and trivy_sbom.get("SchemaVersion") == 2
        and trivy_sbom.get("Trivy") == {"Version": "0.72.0"}
        and trivy_sbom.get("ArtifactName") == sbom_path
        and trivy_sbom.get("ArtifactType") == "cyclonedx"
        and trivy_sbom.get("Metadata")
        == {"OS": {"Family": "wolfi", "Name": "20230201"}}
        and isinstance(trivy_sbom_results, list)
        and [
            (
                result.get("Target"),
                result.get("Class"),
                result.get("Type"),
                (
                    len(result["Packages"])
                    if isinstance(result.get("Packages"), list)
                    else None
                ),
            )
            for result in trivy_sbom_results
            if isinstance(result, Mapping)
        ]
        == [
            (
                f"{sbom_path} (wolfi 20230201)",
                "os-pkgs",
                "wolfi",
                56,
            ),
            ("", "lang-pkgs", "gobinary", 4),
        ],
        code,
        "retained PostgreSQL Trivy SBOM scan scopes changed",
    )
    sbom_scanned_references: set[str] = set()
    sbom_scanned_uids: set[str] = set()
    sbom_scanned_purls: set[str] = set()
    sbom_os_identities: set[tuple[str, str]] = set()
    sbom_scanned_references_by_class: dict[str, set[str]] = {
        "os-pkgs": set(),
        "lang-pkgs": set(),
    }
    for result_index, result in enumerate(trivy_sbom_results):
        _expect(
            isinstance(result, Mapping),
            code,
            f"Trivy SBOM Results[{result_index}] must be an object",
        )
        packages = result.get("Packages")
        _expect(
            isinstance(packages, list) and bool(packages),
            code,
            f"Trivy SBOM Results[{result_index}].Packages must be non-empty",
        )
        for package_index, package in enumerate(packages):
            identifier = (
                package.get("Identifier")
                if isinstance(package, Mapping)
                else None
            )
            component_reference = (
                identifier.get("BOMRef")
                if isinstance(identifier, Mapping)
                else None
            )
            expected_identity = (
                library_components_by_reference.get(component_reference)
                if isinstance(component_reference, str)
                else None
            )
            expected_version = (
                expected_identity[1].removeprefix("go")
                if expected_identity is not None
                and expected_identity[0] == "stdlib"
                else (
                    expected_identity[1]
                    if expected_identity is not None
                    else None
                )
            )
            expected_package_id = (
                f"stdlib@v{expected_version}"
                if expected_identity is not None
                and expected_identity[0] == "stdlib"
                else (
                    f"{expected_identity[0]}@{expected_version}"
                    if expected_identity is not None
                    else None
                )
            )
            _expect(
                isinstance(package, Mapping)
                and isinstance(identifier, Mapping)
                and isinstance(component_reference, str)
                and component_reference
                not in sbom_scanned_references
                and expected_identity is not None
                and package.get("Name") == expected_identity[0]
                and package.get("Version") == expected_version
                and package.get("ID") == expected_package_id
                and isinstance(identifier.get("PURL"), str)
                and bool(identifier["PURL"].strip())
                and identifier["PURL"].casefold()
                == expected_identity[2].casefold()
                and identifier["PURL"] not in sbom_scanned_purls
                and isinstance(identifier.get("UID"), str)
                and bool(identifier["UID"].strip())
                and identifier["UID"] not in sbom_scanned_uids
                and (
                    result.get("Class") != "os-pkgs"
                    or package.get("Arch") == "aarch64"
                ),
                code,
                (
                    f"Trivy SBOM Results[{result_index}].Packages"
                    f"[{package_index}] is not bound to one SBOM library"
                ),
            )
            sbom_scanned_references.add(component_reference)
            sbom_scanned_uids.add(identifier["UID"])
            sbom_scanned_purls.add(identifier["PURL"])
            result_class = result.get("Class")
            _expect(
                isinstance(result_class, str)
                and result_class in sbom_scanned_references_by_class
                and component_reference
                in library_references_by_scan_class[result_class],
                code,
                "Trivy SBOM package is assigned to the wrong scan scope",
            )
            sbom_scanned_references_by_class[result_class].add(
                component_reference
            )
            if result.get("Class") == "os-pkgs":
                sbom_os_identities.add(
                    (package["Name"], package["Version"])
                )
        findings = (
            result["Vulnerabilities"]
            if "Vulnerabilities" in result
            else []
        )
        _expect(
            isinstance(findings, list),
            code,
            (
                f"Trivy SBOM Results[{result_index}].Vulnerabilities "
                "must be a list"
            ),
        )
        for finding in findings:
            _expect(
                isinstance(finding, Mapping)
                and isinstance(finding.get("VulnerabilityID"), str)
                and bool(finding["VulnerabilityID"].strip())
                and isinstance(finding.get("PkgName"), str)
                and bool(finding["PkgName"].strip())
                and isinstance(finding.get("InstalledVersion"), str)
                and bool(finding["InstalledVersion"].strip())
                and isinstance(finding.get("Severity"), str)
                and finding["Severity"] in {"HIGH", "CRITICAL"},
                code,
                (
                    f"Trivy SBOM Results[{result_index}] contains "
                    "a malformed finding"
                ),
            )
            vulnerabilities.append(finding)
    _expect(
        sbom_scanned_references
        == set(library_components_by_reference)
        and sbom_scanned_references_by_class
        == library_references_by_scan_class
        and sbom_os_identities
        == scanned_package_identities
        == apk_library_identities,
        code,
        "Trivy scans do not close the exact CycloneDX library inventory",
    )

    high = sum(item.get("Severity") == "HIGH" for item in vulnerabilities)
    critical = sum(
        item.get("Severity") == "CRITICAL" for item in vulnerabilities
    )
    _expect(
        high == 0 and critical == 0,
        code,
        "retained PostgreSQL scan exceeds the High/Critical threshold",
    )
    trivy_version = _load_json(
        root,
        POSTGRES_EVIDENCE / "trivy-version.json",
        code,
    )
    _expect(
        set(trivy_version) == {"Version", "VulnerabilityDB"}
        and trivy_version.get("Version") == "0.72.0"
        and isinstance(trivy_version.get("VulnerabilityDB"), Mapping)
        and set(trivy_version["VulnerabilityDB"])
        == {"Version", "NextUpdate", "UpdatedAt", "DownloadedAt"}
        and trivy_version["VulnerabilityDB"].get("Version") == 2
        and trivy_version["VulnerabilityDB"].get("UpdatedAt")
        == "2026-07-20T01:09:07.303099965Z",
        code,
        "retained PostgreSQL Trivy database metadata changed",
    )
    trivy_sbom_version = _load_json(
        root,
        POSTGRES_EVIDENCE / "trivy-sbom-version.json",
        code,
    )
    _expect(
        set(trivy_sbom_version) == {"Version", "VulnerabilityDB"}
        and trivy_sbom_version.get("Version") == "0.72.0"
        and isinstance(
            trivy_sbom_version.get("VulnerabilityDB"),
            Mapping,
        )
        and set(trivy_sbom_version["VulnerabilityDB"])
        == {"Version", "NextUpdate", "UpdatedAt", "DownloadedAt"}
        and trivy_sbom_version["VulnerabilityDB"].get("Version") == 2
        and trivy_sbom_version["VulnerabilityDB"].get("UpdatedAt")
        == "2026-07-19T18:43:16.060990559Z",
        code,
        "retained PostgreSQL Trivy SBOM database metadata changed",
    )
    return cyclone, trivy_version, trivy_sbom_version


def _audit_postgres_summary_and_root(
    root: Path,
    cyclone: Mapping[str, Any],
    trivy_version: Mapping[str, Any],
    trivy_sbom_version: Mapping[str, Any],
) -> None:
    code = "POSTGRES_EVIDENCE"
    trusted_root = _load_json(
        root,
        POSTGRES_EVIDENCE / "trusted-root.json",
        code,
    )
    tlogs = trusted_root.get("tlogs")
    certificate_authorities = trusted_root.get("certificateAuthorities")
    ctlogs = trusted_root.get("ctlogs")
    timestamp_authorities = trusted_root.get("timestampAuthorities")
    _expect(
        set(trusted_root)
        == {
            "mediaType",
            "tlogs",
            "certificateAuthorities",
            "ctlogs",
            "timestampAuthorities",
        }
        and trusted_root.get("mediaType")
        == POSTGRES_TRUSTED_ROOT_MEDIA_TYPE
        and isinstance(tlogs, list)
        and len(tlogs) == 2
        and isinstance(certificate_authorities, list)
        and len(certificate_authorities) == 2
        and isinstance(ctlogs, list)
        and len(ctlogs) == 2
        and isinstance(timestamp_authorities, list)
        and len(timestamp_authorities) == 1,
        code,
        "retained Sigstore trusted root changed",
    )
    summary = _load_json(
        root,
        POSTGRES_EVIDENCE / "cryptographic-verification.json",
        code,
    )
    _expect_keysets(
        summary,
        {
            (): {
                "schema_version",
                "collected_at",
                "network_boundary",
                "tools",
                "candidate",
                "publisher",
                "signatures",
                "attestations",
                "slsa",
                "spdx",
                "cyclonedx",
                "trivy",
                "trusted_root",
            },
            ("network_boundary",): {
                "registry_authentication",
                "trusted_root_collection",
                "retained_bundle_reverification",
                "offline_home_write_count",
            },
            ("tools",): {"cosign", "crane", "syft", "trivy"},
            ("signatures",): {"index", "arm64"},
            ("attestations",): {"slsa", "spdx"},
            ("trivy",): {
                "scanner_version",
                "library_component_count",
                "image_scan",
                "sbom_scan",
            },
            ("trivy", "image_scan"): {
                "schema_version",
                "artifact_name",
                "artifact_type",
                "database_updated_at",
                "scope",
                "package_count",
                "high",
                "critical",
            },
            ("trivy", "sbom_scan"): {
                "schema_version",
                "artifact_name",
                "artifact_type",
                "database_updated_at",
                "os_package_count",
                "gobinary_package_count",
                "library_coverage_count",
                "high",
                "critical",
            },
        },
        code,
    )
    _expect_fields(
        summary,
        {
            ("schema_version",): 1,
            ("collected_at",): "2026-07-20T07:31:20Z",
            (
                "network_boundary",
                "registry_authentication",
            ): "anonymous-empty-docker-config",
            (
                "network_boundary",
                "trusted_root_collection",
            ): "online-sigstore-tuf",
            (
                "network_boundary",
                "retained_bundle_reverification",
            ): "offline-denied-proxy",
            ("network_boundary", "offline_home_write_count"): 0,
            ("tools", "cosign"): "3.1.1",
            ("tools", "crane"): "0.21.7",
            ("tools", "syft"): "1.46.0",
            ("tools", "trivy"): "0.72.0",
            ("candidate", "index_reference"): POSTGRES_INDEX,
            (
                "candidate",
                "index_manifest",
                "sha256",
            ): POSTGRES_INDEX.rsplit("@sha256:", 1)[1],
            ("candidate", "index_manifest", "size"): 1_015,
            ("candidate", "arm64_reference"): POSTGRES_ARM64,
            (
                "candidate",
                "arm64_manifest",
                "sha256",
            ): POSTGRES_ARM64.rsplit("@sha256:", 1)[1],
            ("candidate", "arm64_manifest", "size"): 2_510,
            (
                "candidate",
                "attestation_manifest_digest",
            ): POSTGRES_ATTESTATION,
            (
                "candidate",
                "attestation_manifest",
                "sha256",
            ): POSTGRES_ATTESTATION.removeprefix("sha256:"),
            ("candidate", "attestation_manifest", "size"): 29_968,
            (
                "candidate",
                "index_platforms",
            ): ["linux/amd64", "linux/arm64"],
            ("publisher", "identity"): POSTGRES_PUBLISHER_IDENTITY,
            ("publisher", "issuer"): POSTGRES_PUBLISHER_ISSUER,
            (
                "publisher",
                "workflow_name",
            ): POSTGRES_PUBLISHER_WORKFLOW_NAME,
            (
                "publisher",
                "workflow_repository",
            ): POSTGRES_PUBLISHER_REPOSITORY,
            ("publisher", "workflow_ref"): POSTGRES_PUBLISHER_REF,
            (
                "publisher",
                "workflow_trigger",
            ): POSTGRES_PUBLISHER_TRIGGER,
            (
                "signatures",
                "index",
                "workflow_sha",
            ): POSTGRES_RELEASE_WORKFLOW_SHA,
            ("signatures", "index", "rekor_log_index"): 2_181_460_214,
            (
                "signatures",
                "index",
                "rekor_integrated_time",
            ): 1_784_189_273,
            (
                "signatures",
                "arm64",
                "workflow_sha",
            ): POSTGRES_RELEASE_WORKFLOW_SHA,
            ("signatures", "arm64", "rekor_log_index"): 2_181_460_469,
            (
                "signatures",
                "arm64",
                "rekor_integrated_time",
            ): 1_784_189_276,
            (
                "attestations",
                "slsa",
                "workflow_sha",
            ): POSTGRES_SLSA_WORKFLOW_SHA,
            ("attestations", "slsa", "rekor_log_index"): 2_177_108_005,
            (
                "attestations",
                "slsa",
                "rekor_integrated_time",
            ): 1_784_157_617,
            (
                "attestations",
                "spdx",
                "workflow_sha",
            ): POSTGRES_RELEASE_WORKFLOW_SHA,
            ("attestations", "spdx", "rekor_log_index"): 2_181_460_833,
            (
                "attestations",
                "spdx",
                "rekor_integrated_time",
            ): 1_784_189_281,
            ("slsa", "subject_reference"): POSTGRES_ARM64,
            ("slsa", "builder"): POSTGRES_SLSA_BUILDER,
            ("slsa", "build_type"): POSTGRES_SLSA_BUILD_TYPE,
            ("slsa", "invocation_id"): POSTGRES_INDEX,
            ("spdx", "subject_reference"): POSTGRES_ARM64,
            ("spdx", "spdx_version"): "SPDX-2.3",
            ("spdx", "package_count"): 257,
            ("cyclonedx", "bom_format"): "CycloneDX",
            ("cyclonedx", "spec_version"): "1.7",
            ("cyclonedx", "version"): 1,
            ("cyclonedx", "generator"): "syft 1.46.0",
            ("cyclonedx", "component_reference"): POSTGRES_ARM64,
            (
                "cyclonedx",
                "component_count",
            ): len(cyclone.get("components", [])),
            ("trivy", "scanner_version"): "0.72.0",
            ("trivy", "library_component_count"): 60,
            ("trivy", "image_scan", "schema_version"): 2,
            ("trivy", "image_scan", "artifact_name"): POSTGRES_ARM64,
            ("trivy", "image_scan", "artifact_type"): "container_image",
            (
                "trivy",
                "image_scan",
                "database_updated_at",
            ): _nested(trivy_version, "VulnerabilityDB", "UpdatedAt"),
            ("trivy", "image_scan", "scope"): "os-pkgs/wolfi",
            ("trivy", "image_scan", "package_count"): 56,
            ("trivy", "image_scan", "high"): 0,
            ("trivy", "image_scan", "critical"): 0,
            ("trivy", "sbom_scan", "schema_version"): 2,
            (
                "trivy",
                "sbom_scan",
                "artifact_name",
            ): (
                "bootstrap/postgresql/v18.4/evidence/"
                "postgresql-18.4-arm64.cdx.json"
            ),
            ("trivy", "sbom_scan", "artifact_type"): "cyclonedx",
            (
                "trivy",
                "sbom_scan",
                "database_updated_at",
            ): _nested(
                trivy_sbom_version,
                "VulnerabilityDB",
                "UpdatedAt",
            ),
            ("trivy", "sbom_scan", "os_package_count"): 56,
            ("trivy", "sbom_scan", "gobinary_package_count"): 4,
            ("trivy", "sbom_scan", "library_coverage_count"): 60,
            ("trivy", "sbom_scan", "high"): 0,
            ("trivy", "sbom_scan", "critical"): 0,
            (
                "trusted_root",
                "media_type",
            ): POSTGRES_TRUSTED_ROOT_MEDIA_TYPE,
            ("trusted_root", "tlog_count"): 2,
            ("trusted_root", "certificate_authority_count"): 2,
            ("trusted_root", "ctlog_count"): 2,
            ("trusted_root", "timestamp_authority_count"): 1,
        },
        code,
    )


def _reverify_postgres_sigstore_cryptographically(root: Path) -> None:
    code = "POSTGRES_EVIDENCE"
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
        _fail(code, f"cannot inspect Cosign: {error}")
    _expect(
        version.returncode == 0
        and re.search(r"(?m)^GitVersion:\s+v3\.1\.1\s*$", version.stdout)
        is not None,
        code,
        "Cosign 3.1.1 is required for PostgreSQL bundle reverification",
    )
    common = [
        "--trusted-root",
        (POSTGRES_EVIDENCE / "trusted-root.json").as_posix(),
        "--certificate-identity",
        POSTGRES_PUBLISHER_IDENTITY,
        "--certificate-oidc-issuer",
        POSTGRES_PUBLISHER_ISSUER,
        "--certificate-github-workflow-name",
        POSTGRES_PUBLISHER_WORKFLOW_NAME,
        "--certificate-github-workflow-repository",
        POSTGRES_PUBLISHER_REPOSITORY,
        "--certificate-github-workflow-ref",
        POSTGRES_PUBLISHER_REF,
        "--certificate-github-workflow-trigger",
        POSTGRES_PUBLISHER_TRIGGER,
    ]
    with tempfile.TemporaryDirectory(
        prefix="shirokuma-postgres-offline-"
    ) as directory:
        temporary_root = Path(directory)
        home = temporary_root / "home"
        docker = temporary_root / "docker"
        xdg_cache = temporary_root / "xdg-cache"
        xdg_config = temporary_root / "xdg-config"
        xdg_data = temporary_root / "xdg-data"
        temporary_files = temporary_root / "tmp"
        for location in (
            home,
            docker,
            xdg_cache,
            xdg_config,
            xdg_data,
            temporary_files,
        ):
            location.mkdir()
        offline_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "DOCKER_CONFIG": str(docker),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "TMPDIR": str(temporary_files),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "all_proxy": "http://127.0.0.1:9",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        for role, record in POSTGRES_SIGNATURES.items():
            _run_cosign(
                root,
                [
                    "verify-blob",
                    "--timeout",
                    "20s",
                    "--bundle",
                    (
                        POSTGRES_EVIDENCE / str(record["bundle"])
                    ).as_posix(),
                    *common,
                    "--certificate-github-workflow-sha",
                    POSTGRES_RELEASE_WORKFLOW_SHA,
                    (
                        POSTGRES_EVIDENCE / str(record["payload"])
                    ).as_posix(),
                ],
                f"offline PostgreSQL {role} signature verification",
                code=code,
                env=offline_env,
            )
        for role, record in POSTGRES_ATTESTATION_LAYERS.items():
            _run_cosign(
                root,
                [
                    "verify-blob-attestation",
                    "--timeout",
                    "20s",
                    "--bundle",
                    (
                        POSTGRES_EVIDENCE / str(record["bundle"])
                    ).as_posix(),
                    "--type",
                    str(record["predicate_type"]),
                    *common,
                    "--certificate-github-workflow-sha",
                    str(record["workflow_sha"]),
                    (
                        POSTGRES_EVIDENCE / "arm64-manifest.json"
                    ).as_posix(),
                ],
                f"offline PostgreSQL {role} attestation verification",
                code=code,
                env=offline_env,
            )
        written = [
            path
            for path in temporary_root.rglob("*")
            if path.is_file()
        ]
        _expect(
            not written,
            code,
            "offline PostgreSQL verification wrote unexpected state",
        )


def _audit_postgres_evidence(
    root: Path,
    admission: Mapping[str, Any],
    postgres_crypto_verifier: PostgresCryptoVerifier,
) -> None:
    _audit_postgres_evidence_inventory(root, admission)
    for role, record in POSTGRES_SIGNATURES.items():
        _audit_postgres_signature_bundle(root, role, record)
    slsa, spdx = _audit_postgres_manifests(root)
    _audit_postgres_statements(slsa, spdx)
    cyclone, trivy_version, trivy_sbom_version = (
        _audit_postgres_sbom_and_scan(root)
    )
    _audit_postgres_summary_and_root(
        root,
        cyclone,
        trivy_version,
        trivy_sbom_version,
    )
    postgres_crypto_verifier(root)


def _parse_atomic_utc_timestamp(value: Any, label: str) -> datetime:
    match = (
        re.fullmatch(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
            r"(?:\.(\d{1,9}))?Z",
            value,
        )
        if isinstance(value, str)
        else None
    )
    _expect(
        match is not None,
        "ATOMIC_ADMISSION",
        f"{label} must be a canonical UTC timestamp ending in Z",
    )
    assert match is not None
    fraction = match.group(2)
    normalized = match.group(1)
    if fraction is not None:
        normalized += "." + fraction[:6].ljust(6, "0")
    try:
        parsed = datetime.fromisoformat(normalized + "+00:00")
    except ValueError as error:
        _fail("ATOMIC_ADMISSION", f"{label} is not ISO-8601: {error}")
    _expect(
        parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0),
        "ATOMIC_ADMISSION",
        f"{label} must be timezone-aware UTC",
    )
    return parsed


def _atomic_package_identity(name: Any, version: Any) -> tuple[str, str]:
    _expect(
        isinstance(name, str)
        and bool(name.strip())
        and isinstance(version, str)
        and bool(version.strip()),
        "ATOMIC_ADMISSION",
        "scanned package identity must contain non-empty name and version",
    )
    assert isinstance(name, str) and isinstance(version, str)
    normalized_version = (
        version[2:]
        if name.casefold() == "stdlib" and version.startswith("go")
        else version
    )
    return name.casefold(), normalized_version


def _audit_atomic_sbom(
    root: Path,
    relative: Path,
    *,
    component_count: int,
    collect_postgres_libraries: bool = False,
) -> dict[str, set[tuple[str, str]]]:
    sbom = _load_json(root, relative, "ATOMIC_ADMISSION")
    components = sbom.get("components")
    _expect(
        sbom.get("bomFormat") == "CycloneDX"
        and sbom.get("specVersion") == "1.7"
        and isinstance(components, list)
        and len(components) == component_count,
        "ATOMIC_ADMISSION",
        f"{relative} must retain the admitted CycloneDX component closure",
    )
    assert isinstance(components, list)
    references: set[str] = set()
    libraries: dict[str, set[tuple[str, str]]] = {
        "os-pkgs": set(),
        "lang-pkgs": set(),
    }
    for index, component in enumerate(components):
        _expect(
            isinstance(component, Mapping)
            and isinstance(component.get("bom-ref"), str)
            and bool(component["bom-ref"])
            and component["bom-ref"] not in references,
            "ATOMIC_ADMISSION",
            f"{relative} components[{index}] has an invalid or duplicate bom-ref",
        )
        assert isinstance(component, Mapping)
        references.add(component["bom-ref"])
        if not collect_postgres_libraries or component.get("type") != "library":
            continue
        properties = component.get("properties")
        _expect(
            isinstance(properties, list),
            "ATOMIC_ADMISSION",
            f"{relative} library components[{index}] must retain properties",
        )
        assert isinstance(properties, list)
        package_types = [
            item.get("value")
            for item in properties
            if isinstance(item, Mapping)
            and item.get("name") == "syft:package:type"
        ]
        _expect(
            len(package_types) == 1
            and package_types[0] in {"apk", "go-module"},
            "ATOMIC_ADMISSION",
            f"{relative} library components[{index}] has an unscoped package type",
        )
        scope = "os-pkgs" if package_types[0] == "apk" else "lang-pkgs"
        identity = _atomic_package_identity(
            component.get("name"),
            component.get("version"),
        )
        _expect(
            identity not in libraries[scope],
            "ATOMIC_ADMISSION",
            f"{relative} duplicates library identity {identity!r}",
        )
        libraries[scope].add(identity)
    if collect_postgres_libraries:
        _expect(
            {scope: len(values) for scope, values in libraries.items()}
            == {"os-pkgs": 56, "lang-pkgs": 4},
            "ATOMIC_ADMISSION",
            "PostgreSQL SBOM must close the exact 56 APK and 4 Go libraries",
        )
    return libraries


def _audit_atomic_trivy(
    root: Path,
    relative: Path,
    *,
    artifact_name: str,
    artifact_type: str,
    scopes: list[tuple[str, str, int]],
    expected_unknown: int,
) -> dict[str, set[tuple[str, str]]]:
    report = _load_json(root, relative, "ATOMIC_ADMISSION")
    results = report.get("Results")
    _expect(
        report.get("ArtifactName") == artifact_name
        and report.get("ArtifactType") == artifact_type
        and isinstance(results, list)
        and len(results) == len(scopes),
        "ATOMIC_ADMISSION",
        f"{relative} does not bind the admitted artifact and exact scan scopes",
    )
    assert isinstance(results, list)
    scanned: dict[str, set[tuple[str, str]]] = {}
    severity_counts = {
        "UNKNOWN": 0,
        "LOW": 0,
        "MEDIUM": 0,
        "HIGH": 0,
        "CRITICAL": 0,
    }
    for index, (result, expected_scope) in enumerate(zip(results, scopes)):
        result_class, result_type, package_count = expected_scope
        _expect(
            isinstance(result, Mapping)
            and result.get("Class") == result_class
            and result.get("Type") == result_type
            and isinstance(result.get("Packages"), list)
            and len(result["Packages"]) == package_count,
            "ATOMIC_ADMISSION",
            f"{relative} Results[{index}] changed scope or package count",
        )
        assert isinstance(result, Mapping)
        packages = result["Packages"]
        assert isinstance(packages, list)
        identities: set[tuple[str, str]] = set()
        package_records: set[tuple[str, str, str, str]] = set()
        for package_index, package in enumerate(packages):
            _expect(
                isinstance(package, Mapping)
                and isinstance(package.get("Identifier"), Mapping)
                and isinstance(package["Identifier"].get("PURL"), str)
                and bool(package["Identifier"]["PURL"].strip())
                and isinstance(package["Identifier"].get("UID"), str)
                and bool(package["Identifier"]["UID"].strip())
                and (
                    "BOMRef" not in package["Identifier"]
                    or isinstance(package["Identifier"]["BOMRef"], str)
                )
                and (
                    "FilePath" not in package
                    or isinstance(package["FilePath"], str)
                ),
                "ATOMIC_ADMISSION",
                f"{relative} Results[{index}].Packages[{package_index}] is unbound",
            )
            assert isinstance(package, Mapping)
            identifier = package["Identifier"]
            package_record = (
                identifier["PURL"],
                identifier["UID"],
                str(identifier.get("BOMRef", "")),
                str(package.get("FilePath", "")),
            )
            _expect(
                package_record not in package_records,
                "ATOMIC_ADMISSION",
                f"{relative} Results[{index}] duplicates canonical package "
                f"identity {package_record!r}",
            )
            package_records.add(package_record)
            identity = _atomic_package_identity(
                package.get("Name"),
                package.get("Version"),
            )
            identities.add(identity)
        scanned[result_class] = identities
        vulnerabilities: Any
        if "Vulnerabilities" not in result:
            vulnerabilities = []
        else:
            vulnerabilities = result["Vulnerabilities"]
            _expect(
                isinstance(vulnerabilities, list),
                "ATOMIC_ADMISSION",
                f"{relative} Results[{index}].Vulnerabilities "
                "must be an array when present",
            )
        assert isinstance(vulnerabilities, list)
        for vulnerability_index, vulnerability in enumerate(vulnerabilities):
            _expect(
                isinstance(vulnerability, Mapping)
                and isinstance(vulnerability.get("Severity"), str)
                and vulnerability["Severity"] in severity_counts,
                "ATOMIC_ADMISSION",
                f"{relative} Results[{index}].Vulnerabilities"
                f"[{vulnerability_index}] has invalid severity",
            )
            assert isinstance(vulnerability, Mapping)
            severity_counts[vulnerability["Severity"]] += 1
            vulnerability_identity = _atomic_package_identity(
                vulnerability.get("PkgName"),
                vulnerability.get("InstalledVersion"),
            )
            _expect(
                vulnerability_identity in identities,
                "ATOMIC_ADMISSION",
                f"{relative} vulnerability is outside its exact package scope",
            )
    _expect(
        severity_counts["HIGH"] == 0
        and severity_counts["CRITICAL"] == 0
        and severity_counts["UNKNOWN"] == expected_unknown,
        "ATOMIC_ADMISSION",
        f"{relative} violates the admitted High/Critical/Unknown finding counts",
    )
    return scanned


def _audit_atomic_postgres_reference_partition(root: Path) -> None:
    code = "ATOMIC_ADMISSION"

    def canonical_component_purl(value: str, package_type: str) -> str:
        base, separator, query = value.partition("?")
        if package_type == "apk":
            match = re.fullmatch(r"(pkg:apk/wolfi/)([^@]+)(@.+)", base)
            _expect(
                match is not None,
                code,
                "PostgreSQL APK component PURL is noncanonical",
            )
            assert match is not None
            name_parts = re.split(r"(%[0-9A-Fa-f]{2})", match.group(2))
            canonical_name = "".join(
                part.upper() if part.startswith("%") else part.casefold()
                for part in name_parts
            )
            base = match.group(1) + canonical_name + match.group(3)
        parameters = [
            parameter
            for parameter in query.split("&")
            if parameter and not parameter.startswith("package-id=")
        ]
        return base + (separator + "&".join(parameters) if parameters else "")

    sbom = _load_json(
        root,
        ATOMIC_EVIDENCE / "postgresql-18.4-arm64.cdx.json",
        code,
    )
    report = _load_json(
        root,
        ATOMIC_EVIDENCE / "postgresql-trivy-sbom.json",
        code,
    )
    expected_references = {"os-pkgs": set(), "lang-pkgs": set()}
    expected_purls = {"os-pkgs": set(), "lang-pkgs": set()}
    components = sbom.get("components")
    _expect(
        isinstance(components, list),
        code,
        "PostgreSQL SBOM components must be an array",
    )
    assert isinstance(components, list)
    for component in components:
        if not isinstance(component, Mapping) or component.get("type") != "library":
            continue
        properties = component.get("properties")
        _expect(
            isinstance(properties, list),
            code,
            "PostgreSQL SBOM library properties must be an array",
        )
        assert isinstance(properties, list)
        package_types = [
            item.get("value")
            for item in properties
            if isinstance(item, Mapping)
            and item.get("name") == "syft:package:type"
        ]
        _expect(
            len(package_types) == 1
            and package_types[0] in {"apk", "go-module"},
            code,
            "PostgreSQL SBOM library package type is not closed",
        )
        scope = "os-pkgs" if package_types[0] == "apk" else "lang-pkgs"
        reference = component.get("bom-ref")
        purl = component.get("purl")
        _expect(
            isinstance(reference, str)
            and bool(reference)
            and isinstance(purl, str)
            and bool(purl),
            code,
            "PostgreSQL SBOM library must retain bom-ref and PURL",
        )
        _expect(
            canonical_component_purl(purl, package_types[0])
            == canonical_component_purl(reference, package_types[0]),
            code,
            "PostgreSQL SBOM component PURL does not canonically bind bom-ref",
        )
        expected_references[scope].add(reference)
        expected_purls[scope].add(
            canonical_component_purl(purl, package_types[0])
        )

    actual_references = {"os-pkgs": set(), "lang-pkgs": set()}
    actual_purls = {"os-pkgs": set(), "lang-pkgs": set()}
    results = report.get("Results")
    _expect(
        isinstance(results, list),
        code,
        "PostgreSQL SBOM scan results must be an array",
    )
    assert isinstance(results, list)
    for result in results:
        _expect(
            isinstance(result, Mapping)
            and result.get("Class") in actual_references
            and isinstance(result.get("Packages"), list),
            code,
            "PostgreSQL SBOM scan scope is invalid",
        )
        assert isinstance(result, Mapping)
        scope = result["Class"]
        for package in result["Packages"]:
            _expect(
                isinstance(package, Mapping)
                and isinstance(package.get("Identifier"), Mapping)
                and isinstance(package["Identifier"].get("BOMRef"), str)
                and bool(package["Identifier"]["BOMRef"])
                and isinstance(package["Identifier"].get("PURL"), str)
                and bool(package["Identifier"]["PURL"]),
                code,
                "PostgreSQL SBOM scan package lacks PURL/BOMRef",
            )
            identifier = package["Identifier"]
            package_type = "apk" if scope == "os-pkgs" else "go-module"
            _expect(
                identifier["PURL"]
                == canonical_component_purl(
                    identifier["BOMRef"],
                    package_type,
                ),
                code,
                "PostgreSQL SBOM scan package PURL and BOMRef must bind "
                "the same canonical component",
            )
            actual_references[scope].add(identifier["BOMRef"])
            actual_purls[scope].add(identifier["PURL"])
    _expect(
        actual_references == expected_references
        and actual_purls == expected_purls,
        code,
        "fresh PostgreSQL SBOM scan must preserve the exact PURL/BOMRef partition",
    )

    image_report = _load_json(
        root,
        ATOMIC_EVIDENCE / "postgresql-trivy.json",
        code,
    )
    image_results = image_report.get("Results")
    _expect(
        isinstance(image_results, list) and len(image_results) == 1,
        code,
        "fresh PostgreSQL image scan must retain one exact OS scope",
    )
    assert isinstance(image_results, list)
    image_purls: set[str] = set()
    for package in image_results[0]["Packages"]:
        purl = package["Identifier"]["PURL"]
        _expect(
            purl.startswith("pkg:apk/wolfi/") and purl not in image_purls,
            code,
            "fresh PostgreSQL image scan APK PURLs must be canonical and unique",
        )
        image_purls.add(purl)
    _expect(
        len(image_purls) == 56,
        code,
        "fresh PostgreSQL image scan must bind all 56 APK PURLs",
    )


def _audit_atomic_admission(root: Path) -> Mapping[str, Any]:
    code = "ATOMIC_ADMISSION"
    receipt_hash, _ = _sha256_and_size(
        root,
        POLARIS_ATOMIC_ADMISSION,
        code,
    )
    _expect(
        receipt_hash == POLARIS_ATOMIC_ADMISSION_SHA256,
        code,
        "atomic admission receipt differs from the admitted checkpoint",
    )
    receipt = _load_json(root, POLARIS_ATOMIC_ADMISSION, code)
    receipt_database_updated_at = _nested(
        receipt,
        "vulnerability_database",
        "updated_at",
    )
    expected_receipt = {
        "schema_version": 1,
        "admission": "approved",
        "state": "admitted",
        "decision_at": "2026-07-20T09:15:43.853Z",
        "platform": "linux/arm64",
        "components": [
            {
                "component": "polaris",
                "version": POLARIS_VERSION,
                "reference": POLARIS_IMAGE_REFERENCE,
            },
            {
                "component": "postgresql",
                "version": "18.4",
                "reference": POSTGRES_ARM64,
            },
        ],
        "primary_evidence_manifest": {
            "path": ATOMIC_EVIDENCE_MANIFEST.as_posix(),
            "sha256": ATOMIC_EVIDENCE_MANIFEST_SHA256,
            "size": ATOMIC_EVIDENCE_MANIFEST_SIZE,
            "entries": len(ATOMIC_EVIDENCE_PRIMARY),
        },
        "preflight": {
            "path": (ATOMIC_EVIDENCE / "anonymous-preflight.json").as_posix(),
            "sha256": ATOMIC_EVIDENCE_PRIMARY["anonymous-preflight.json"],
            "preflighted_at": "2026-07-20T09:15:43.853Z",
            "network_boundary": "anonymous-empty-docker-config",
            "tool": {"name": "crane", "version": "0.21.7"},
            "entries": 3,
        },
        "vulnerability_database": {
            "path": (ATOMIC_EVIDENCE / "trivy-version.json").as_posix(),
            "sha256": ATOMIC_EVIDENCE_PRIMARY["trivy-version.json"],
            "updated_at": receipt_database_updated_at,
            "maximum_age_hours_at_decision": 24,
        },
        "scans": {
            "polaris": {
                "sbom": {
                    "path": (
                        ATOMIC_EVIDENCE / "polaris-1.6.0-arm64.cdx.json"
                    ).as_posix(),
                    "sha256": ATOMIC_EVIDENCE_PRIMARY[
                        "polaris-1.6.0-arm64.cdx.json"
                    ],
                    "format": "CycloneDX",
                    "spec_version": "1.7",
                    "component_count": 6_731,
                },
                "vulnerability_scan": {
                    "path": (
                        ATOMIC_EVIDENCE / "polaris-trivy.json"
                    ).as_posix(),
                    "sha256": ATOMIC_EVIDENCE_PRIMARY["polaris-trivy.json"],
                    "artifact_reference": POLARIS_IMAGE_REFERENCE,
                    "scopes": [
                        {
                            "class": "os-pkgs",
                            "type": "amazon",
                            "package_count": 133,
                        },
                        {
                            "class": "lang-pkgs",
                            "type": "jar",
                            "package_count": 456,
                        },
                    ],
                    "high": 0,
                    "critical": 0,
                },
            },
            "postgresql": {
                "sbom": {
                    "path": (
                        ATOMIC_EVIDENCE / "postgresql-18.4-arm64.cdx.json"
                    ).as_posix(),
                    "sha256": ATOMIC_EVIDENCE_PRIMARY[
                        "postgresql-18.4-arm64.cdx.json"
                    ],
                    "format": "CycloneDX",
                    "spec_version": "1.7",
                    "component_count": 4_725,
                },
                "image_scan": {
                    "path": (
                        ATOMIC_EVIDENCE / "postgresql-trivy.json"
                    ).as_posix(),
                    "sha256": ATOMIC_EVIDENCE_PRIMARY["postgresql-trivy.json"],
                    "artifact_reference": POSTGRES_ARM64,
                    "scopes": [
                        {
                            "class": "os-pkgs",
                            "type": "wolfi",
                            "package_count": 56,
                        }
                    ],
                    "high": 0,
                    "critical": 0,
                },
                "sbom_scan": {
                    "path": (
                        ATOMIC_EVIDENCE / "postgresql-trivy-sbom.json"
                    ).as_posix(),
                    "sha256": ATOMIC_EVIDENCE_PRIMARY[
                        "postgresql-trivy-sbom.json"
                    ],
                    "artifact_path": (
                        ATOMIC_EVIDENCE / "postgresql-18.4-arm64.cdx.json"
                    ).as_posix(),
                    "scopes": [
                        {
                            "class": "os-pkgs",
                            "type": "wolfi",
                            "package_count": 56,
                        },
                        {
                            "class": "lang-pkgs",
                            "type": "gobinary",
                            "package_count": 4,
                        },
                    ],
                    "unknown": 1,
                    "high": 0,
                    "critical": 0,
                },
            },
        },
        "runtime": {
            "permitted": False,
            "next_boundary": "runtime_acceptance_pending",
        },
    }
    _expect(
        _json_equal_type_sensitive(receipt, expected_receipt),
        code,
        "atomic admission receipt schema or values changed",
    )

    directory = root / ATOMIC_EVIDENCE
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        code,
        "atomic admission evidence directory is invalid",
    )
    expected_names = (
        set(ATOMIC_EVIDENCE_PRIMARY)
        | {"evidence.sha256", "supply-chain.json"}
    )
    actual_names: set[str] = set()
    for path in directory.iterdir():
        _expect(
            path.is_file() and not path.is_symlink(),
            code,
            f"atomic evidence must be a regular file: {path.name}",
        )
        actual_names.add(path.name)
    _expect(
        actual_names == expected_names,
        code,
        "atomic admission evidence directory must be the exact 9-file closure",
    )
    manifest_hash, manifest_size = _sha256_and_size(
        root,
        ATOMIC_EVIDENCE_MANIFEST,
        code,
    )
    _expect(
        (manifest_hash, manifest_size)
        == (ATOMIC_EVIDENCE_MANIFEST_SHA256, ATOMIC_EVIDENCE_MANIFEST_SIZE),
        code,
        "atomic primary evidence manifest differs from the receipt",
    )
    try:
        lines = (root / ATOMIC_EVIDENCE_MANIFEST).read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as error:
        _fail(code, f"cannot read atomic evidence manifest: {error}")
    parsed: dict[str, str] = {}
    order: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        _expect(
            match is not None,
            code,
            "atomic evidence manifest contains a noncanonical record",
        )
        assert match is not None
        name = match.group(2)
        _expect(
            name not in parsed,
            code,
            f"atomic evidence manifest duplicates {name}",
        )
        parsed[name] = match.group(1)
        order.append(name)
    _expect(
        parsed == ATOMIC_EVIDENCE_PRIMARY and order == sorted(order),
        code,
        "atomic evidence manifest must close the exact seven primary payloads",
    )
    for name, wanted_hash in parsed.items():
        actual_hash, size = _sha256_and_size(
            root,
            ATOMIC_EVIDENCE / name,
            code,
        )
        _expect(
            actual_hash == wanted_hash and size > 0,
            code,
            f"{name} differs from the atomic primary evidence manifest",
        )
    _expect(
        _is_regular_file_without_symlink_components(
            root,
            ATOMIC_SUPPLY_CHAIN,
        )
        and (root / ATOMIC_SUPPLY_CHAIN).stat().st_size > 0,
        code,
        "atomic supply-chain.json must be retained beside the primary manifest",
    )

    preflight = _load_json(
        root,
        ATOMIC_EVIDENCE / "anonymous-preflight.json",
        code,
    )
    expected_preflight = {
        "schema_version": 1,
        "preflighted_at": receipt["decision_at"],
        "network_boundary": "anonymous-empty-docker-config",
        "tool": {"name": "crane", "version": "0.21.7"},
        "entries": [
            {
                "component": "polaris",
                "role": "runtime",
                "reference": POLARIS_IMAGE_REFERENCE,
                "manifest_sha256": POLARIS_IMAGE_DIGEST.removeprefix(
                    "sha256:"
                ),
                "manifest_size": 2_005,
                "anonymous": True,
            },
            {
                "component": "postgresql",
                "role": "index",
                "reference": POSTGRES_INDEX,
                "manifest_sha256": POSTGRES_INDEX.rsplit("sha256:", 1)[1],
                "manifest_size": 1_015,
                "anonymous": True,
            },
            {
                "component": "postgresql",
                "role": "runtime",
                "reference": POSTGRES_ARM64,
                "manifest_sha256": POSTGRES_ARM64.rsplit("sha256:", 1)[1],
                "manifest_size": 2_510,
                "anonymous": True,
            },
        ],
    }
    _expect(
        _json_equal_type_sensitive(preflight, expected_preflight),
        code,
        "anonymous exact-digest preflight changed",
    )

    version = _load_json(
        root,
        ATOMIC_EVIDENCE / "trivy-version.json",
        code,
    )
    database = version.get("VulnerabilityDB")
    _expect(
        set(version) == {"Version", "VulnerabilityDB"}
        and version.get("Version") == "0.72.0"
        and isinstance(database, Mapping)
        and set(database)
        == {"Version", "NextUpdate", "UpdatedAt", "DownloadedAt"}
        and database.get("Version") == 2
        and database.get("UpdatedAt")
        == receipt["vulnerability_database"]["updated_at"],
        code,
        "fresh Trivy database metadata changed",
    )
    assert isinstance(database, Mapping)
    decision_at = _parse_atomic_utc_timestamp(
        receipt["decision_at"],
        "decision_at",
    )
    database_updated_at = _parse_atomic_utc_timestamp(
        database["UpdatedAt"],
        "VulnerabilityDB.UpdatedAt",
    )
    database_age = decision_at - database_updated_at
    _expect(
        timedelta(0)
        <= database_age
        <= timedelta(
            hours=receipt["vulnerability_database"][
                "maximum_age_hours_at_decision"
            ]
        ),
        code,
        "Trivy database must be no more than 24 hours old at decision_at",
    )

    _audit_atomic_sbom(
        root,
        ATOMIC_EVIDENCE / "polaris-1.6.0-arm64.cdx.json",
        component_count=6_731,
    )
    postgres_libraries = _audit_atomic_sbom(
        root,
        ATOMIC_EVIDENCE / "postgresql-18.4-arm64.cdx.json",
        component_count=4_725,
        collect_postgres_libraries=True,
    )
    _audit_atomic_trivy(
        root,
        ATOMIC_EVIDENCE / "polaris-trivy.json",
        artifact_name=POLARIS_IMAGE_REFERENCE,
        artifact_type="container_image",
        scopes=[
            ("os-pkgs", "amazon", 133),
            ("lang-pkgs", "jar", 456),
        ],
        expected_unknown=0,
    )
    postgres_image_scan = _audit_atomic_trivy(
        root,
        ATOMIC_EVIDENCE / "postgresql-trivy.json",
        artifact_name=POSTGRES_ARM64,
        artifact_type="container_image",
        scopes=[("os-pkgs", "wolfi", 56)],
        expected_unknown=0,
    )
    postgres_sbom_scan = _audit_atomic_trivy(
        root,
        ATOMIC_EVIDENCE / "postgresql-trivy-sbom.json",
        artifact_name=(
            ATOMIC_EVIDENCE / "postgresql-18.4-arm64.cdx.json"
        ).as_posix(),
        artifact_type="cyclonedx",
        scopes=[
            ("os-pkgs", "wolfi", 56),
            ("lang-pkgs", "gobinary", 4),
        ],
        expected_unknown=1,
    )
    _audit_atomic_postgres_reference_partition(root)
    _expect(
        postgres_image_scan == {"os-pkgs": postgres_libraries["os-pkgs"]}
        and postgres_sbom_scan == postgres_libraries,
        code,
        "fresh PostgreSQL image and SBOM scans must close the exact dual scope",
    )
    return receipt


def _audit_pending_files(root: Path) -> None:
    for relative in FORBIDDEN_PENDING_PATHS:
        _expect(
            not (root / relative).exists(),
            "FORBIDDEN_PATH",
            f"{relative} must remain absent before image evidence review",
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
    _expect(
        workflow_inventory == REVIEW_PENDING_WORKFLOW_INVENTORY,
        "FORBIDDEN_PATH",
        "workflow inventory changed while image publication is pending; "
        f"expected {sorted(REVIEW_PENDING_WORKFLOW_INVENTORY)}, "
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
        expected_retained = (
            sorted({"README.md", *POLARIS_DEPENDENCY_EVIDENCE_RECORDS})
            if evidence_root == POLARIS_EVIDENCE
            else sorted(
                {
                    "README.md",
                    "evidence.sha256",
                    *POSTGRES_EVIDENCE_REQUIRED,
                }
            )
        )
        _expect(
            retained == expected_retained,
            "FORBIDDEN_PATH",
            f"{evidence_root} must contain exactly {expected_retained} "
            "while admission is blocked",
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


def _bound_runtime_acceptance_receipt(root: Path) -> Path | None:
    contract_path = root / POLARIS_RUNTIME_ACTIVATION_CONTRACT
    if not contract_path.is_file():
        return None
    contract = _load_json(
        root,
        POLARIS_RUNTIME_ACTIVATION_CONTRACT,
        "FORBIDDEN_PATH",
    )
    live_acceptance = contract.get("live_acceptance")
    if (
        contract.get("schema_version") != 2
        or contract.get("state") != "runtime_accepted"
        or not isinstance(live_acceptance, Mapping)
        or live_acceptance.get("complete") is not True
        or live_acceptance.get("receipt")
        != POLARIS_RUNTIME_ACCEPTANCE_RECEIPT.as_posix()
    ):
        return None
    expected_sha256 = live_acceptance.get("receipt_sha256")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        return None
    observed_sha256, _ = _sha256_and_size(
        root,
        POLARIS_RUNTIME_ACCEPTANCE_RECEIPT,
        "FORBIDDEN_PATH",
    )
    if observed_sha256 != expected_sha256:
        return None
    return POLARIS_RUNTIME_ACCEPTANCE_RECEIPT


def _bound_runtime_acceptance_receipts(root: Path) -> set[Path]:
    primary = _bound_runtime_acceptance_receipt(root)
    if primary is None:
        return set()
    contract = _load_json(
        root,
        POLARIS_RUNTIME_ACTIVATION_CONTRACT,
        "FORBIDDEN_PATH",
    )
    live_acceptance = contract["live_acceptance"]
    additional = live_acceptance.get("additional_receipts")
    if additional is None:
        return {primary}
    if (
        not isinstance(additional, list)
        or len(additional) != 1
        or not isinstance(additional[0], Mapping)
        or set(additional[0]) != {"receipt", "receipt_sha256"}
        or additional[0].get("receipt")
        != ICEBERG_RUNTIME_ACCEPTANCE_RECEIPT.as_posix()
    ):
        return set()
    expected_sha256 = additional[0].get("receipt_sha256")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        return set()
    observed_sha256, _ = _sha256_and_size(
        root,
        ICEBERG_RUNTIME_ACCEPTANCE_RECEIPT,
        "FORBIDDEN_PATH",
    )
    if observed_sha256 != expected_sha256:
        return set()
    return {primary, ICEBERG_RUNTIME_ACCEPTANCE_RECEIPT}


def _audit_retained_pending_evidence(root: Path) -> None:
    directory = root / RETAINED_EVIDENCE_ROOT
    if not directory.exists():
        return
    accepted_runtime_receipts = _bound_runtime_acceptance_receipts(root)
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "FORBIDDEN_PATH",
        f"invalid retained evidence root: {RETAINED_EVIDENCE_ROOT}",
    )
    for path in directory.rglob("*"):
        atomic_directory = root / ATOMIC_EVIDENCE
        admin_admission_directory = root / POLARIS_ADMIN_ADMISSION_EVIDENCE
        if (
            path == atomic_directory
            or atomic_directory in path.parents
            or path == admin_admission_directory
            or admin_admission_directory in path.parents
        ):
            continue
        relative = path.relative_to(root).as_posix()
        is_accepted_runtime_receipt = (
            path.relative_to(root) in accepted_runtime_receipts
        )
        evidence_relative = path.relative_to(directory).as_posix()
        path_tokens = _path_identity_tokens(evidence_relative)
        _expect(
            is_accepted_runtime_receipt
            or not any(
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
                is_accepted_runtime_receipt
                or not _is_pending_evidence_identity([text]),
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
            is_accepted_runtime_receipt
            or not any(
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
    code = "LEDGER_BLOCK"
    ledger = _load_json(root, RESIDENT_LEDGER, code)
    images = ledger.get("images")
    _expect(isinstance(images, list), code, "ledger images must be a list")
    assert isinstance(images, list)
    aliases = {
        "apachepolaris",
        "chainguardpostgres",
        "polaris",
        "polarisadmin",
        "postgres",
        "postgresql",
    }
    admitted: list[Mapping[str, Any]] = []
    for index, entry in enumerate(images):
        _expect(
            isinstance(entry, Mapping),
            code,
            f"ledger images[{index}] must be an object",
        )
        assert isinstance(entry, Mapping)
        component = str(entry.get("component", ""))
        normalized_component = re.sub(r"[^a-z0-9]", "", component.casefold())
        serialized = json.dumps(entry, sort_keys=True).casefold()
        identity = " ".join(
            str(entry.get(field, ""))
            for field in ("component", "reference", "source")
        ).casefold()
        catalog_identity = any(
            marker in str(entry.get(field, "")).casefold()
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
            admitted.append(entry)
    expected = [
        {
            "component": "polaris",
            "version": POLARIS_VERSION,
            "source": "https://github.com/apache/polaris",
            "platform": "linux/arm64",
            "reference": POLARIS_IMAGE_REFERENCE,
            "sbom_artifact": (
                "evidence/polaris-v1.6.0-postgresql-v18.4/"
                "polaris-1.6.0-arm64.cdx.json"
            ),
            "scan_artifact": (
                "evidence/polaris-v1.6.0-postgresql-v18.4/"
                "polaris-trivy.json"
            ),
            "supply_chain_artifact": (
                "evidence/polaris-v1.6.0-postgresql-v18.4/"
                "supply-chain.json"
            ),
            "sbom_generator": "syft 1.46.0",
            "scanner_version": "trivy 0.72.0",
            "vulnerability_db_updated_at": (
                "2026-07-19T18:43:16.060990559Z"
            ),
        },
        {
            "component": "postgresql",
            "version": "18.4",
            "source": (
                "https://github.com/chainguard-images/images/"
                "tree/main/images/postgres"
            ),
            "platform": "linux/arm64",
            "reference": POSTGRES_ARM64,
            "sbom_artifact": (
                "evidence/polaris-v1.6.0-postgresql-v18.4/"
                "postgresql-18.4-arm64.cdx.json"
            ),
            "scan_artifact": (
                "evidence/polaris-v1.6.0-postgresql-v18.4/"
                "postgresql-trivy.json"
            ),
            "supply_chain_artifact": (
                "evidence/polaris-v1.6.0-postgresql-v18.4/"
                "supply-chain.json"
            ),
            "sbom_generator": "syft 1.46.0",
            "scanner_version": "trivy 0.72.0",
            "vulnerability_db_updated_at": (
                "2026-07-19T18:43:16.060990559Z"
            ),
        },
        {
            "component": "polaris-admin",
            "version": "1.6.0",
            "source": "https://github.com/apache/polaris",
            "platform": "linux/arm64",
            "reference": (
                "ghcr.io/tommykammy/shirokuma-polaris-admin@"
                "sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd"
            ),
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
            "vulnerability_db_updated_at": (
                "2026-07-21T01:08:43.916306317Z"
            ),
        },
    ]
    observed_components = sorted(
        str(entry.get("component", "<unnamed>")) for entry in admitted
    )
    missing_components = sorted(
        {"polaris", "postgresql", "polaris-admin"} - set(observed_components)
    )
    _expect(
        _json_equal_type_sensitive(admitted, expected),
        code,
        "resident ledger must contain the exact Polaris/PostgreSQL pair plus "
        f"the admitted Admin image; missing {missing_components}, "
        f"found {observed_components}",
    )
    for entry in admitted:
        for field in (
            "sbom_artifact",
            "scan_artifact",
            "supply_chain_artifact",
        ):
            relative = Path("security") / str(entry[field])
            _expect(
                _is_regular_file_without_symlink_components(root, relative),
                code,
                f"{entry['component']} {field} is not retained: {relative}",
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


def audit_publication_bootstrap(root: Path) -> None:
    """Validate the retired publisher and post-publication contract statically."""

    root = root.resolve()
    _audit_source(root)
    _audit_contract(root)
    _audit_polaris_admission(root)


def audit(
    root: Path,
    *,
    dependency_crypto_verifier: Optional[DependencyCryptoVerifier] = None,
    image_crypto_verifier: Optional[ImageCryptoVerifier] = None,
    postgres_crypto_verifier: Optional[PostgresCryptoVerifier] = None,
) -> None:
    root = root.resolve()
    if dependency_crypto_verifier is None:
        dependency_crypto_verifier = (
            _reverify_dependency_sigstore_cryptographically
        )
    if image_crypto_verifier is None:
        image_crypto_verifier = _reverify_image_sigstore_cryptographically
    if postgres_crypto_verifier is None:
        postgres_crypto_verifier = (
            _reverify_postgres_sigstore_cryptographically
        )
    _audit_source(root)
    contract = _audit_contract(root)
    _audit_polaris_admission(root)
    _audit_admin_build_inputs_retained_evidence(root)
    _audit_dependency_publication_evidence(
        root,
        contract,
        dependency_crypto_verifier,
    )
    _audit_image_publication_evidence(root, image_crypto_verifier)
    postgres_admission = _audit_postgres_admission(root)
    _audit_postgres_evidence(
        root,
        postgres_admission,
        postgres_crypto_verifier,
    )
    _audit_atomic_admission(root)
    _audit_pending_files(root)
    _audit_admin_image_publication_policy(root)
    _audit_retained_pending_evidence(root)
    _audit_ledger(root)
    runtime_activation = root / "security/polaris-runtime-activation.json"
    if runtime_activation.is_file():
        try:
            from scripts.verify_polaris_runtime import (
                RuntimeContractError,
                audit as audit_runtime_activation,
            )
        except ModuleNotFoundError:
            from verify_polaris_runtime import (  # type: ignore[no-redef]
                RuntimeContractError,
                audit as audit_runtime_activation,
            )

        try:
            audit_runtime_activation(root)
        except RuntimeContractError as error:
            _fail(error.code, error.detail)
    else:
        _audit_pending_runtime_inventory(root)
        _audit_runtime_absence(root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--root", type=Path, default=Path("."))
    bootstrap_parser = subparsers.add_parser("audit-publication-bootstrap")
    bootstrap_parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "audit-publication-bootstrap":
            audit_publication_bootstrap(args.root)
        else:
            audit(args.root)
    except ContractError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.command == "audit-publication-bootstrap":
        print(
            "polaris-trusted-image: retired publisher and evidence contract "
            "are bound; "
            "cryptographic evidence remains unverified"
        )
        return 0
    runtime_state = (
        "live runtime acceptance is bound"
        if (args.root / POLARIS_RUNTIME_ACTIVATION_CONTRACT).is_file()
        else "runtime remains fail-closed pending activation"
    )
    print(
        "polaris-trusted-image: atomic Polaris/PostgreSQL and separate Admin "
        "admissions pass; reviewed evidence, exact-digest preflight, and the "
        f"resident ledger are bound; {runtime_state}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
