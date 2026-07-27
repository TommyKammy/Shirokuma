#!/usr/bin/env python3
"""Fail-closed verifier for retained Trino 483 dependency evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_DIR = Path("bootstrap/trino/v483/dependency-evidence")
CONTRACT_PATH = Path("bootstrap/trino/v483/trusted-build-contract.json")
ADMISSION_PATH = Path("bootstrap/trino/v483/admission.json")
ACTIVE_WORKFLOW_PATH = Path(".github/workflows/trino-maven-dependencies.yml")
HISTORICAL_WORKFLOW_PATH = EVIDENCE_DIR / "historical-publisher-workflow.yml"

REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@sha256:"
    "0394143034298f4c6606c288e8ef97154826978bf3aa97e1e952499f8af5075c"
)
DIGEST = "sha256:0394143034298f4c6606c288e8ef97154826978bf3aa97e1e952499f8af5075c"
SOURCE_SHA = "1ae1996eaf654e69daad60c574c7abb4e4d2be3b"
RUN_ID = "30231656483"
RUN_ATTEMPT = "1"
PUBLICATION_SHA256 = "6248b967b48c574b04cd757cb23b7ca291658be15b133bb4df2a005d29c4bfb2"
HISTORICAL_WORKFLOW_SHA256 = (
    "3f1750bf0f81b6a8859af81c244eaf5aee16f520dd84ea22207f8bd9c9004c3f"
)
ISSUER = "https://token.actions.githubusercontent.com"
IDENTITY = (
    "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
    "trino-maven-dependencies.yml@refs/heads/main"
)
ARTIFACT_FILES = {
    "anonymous-pull.json",
    "bun-dependency-manifest.json",
    "cosign-provenance-bundle.json",
    "cosign-signature-bundle.json",
    "cosign-verify-attestation.json",
    "cosign-verify.json",
    "independent-reconstruction.json",
    "maven-dependency-manifest.json",
    "oci-manifest.json",
    "offline-build.json",
    "publication.json",
    "react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json",
    "slsa-provenance.json",
    "toolchain.json",
    "trino-bun-dependencies-483.cdx.json",
    "trino-maven-dependencies-483.cdx.json",
    "trivy-bun-vulnerability-raw.json",
    "trivy-bun-vulnerability.json",
    "trivy-version.json",
    "trivy-vulnerability.json",
}
DIRECTORY_FILES = ARTIFACT_FILES | {"README.md", "historical-publisher-workflow.yml"}


class EvidenceError(RuntimeError):
    """Raised when retained evidence crosses or weakens the review boundary."""


def _fail(code: str, detail: str) -> None:
    raise EvidenceError(f"{code}: {detail}")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail("JSON", f"{path}: {error}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        _fail("FILE", f"{path}: {error}")
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail("SCHEMA", f"{label} must be an object")
    return value


def _vulnerabilities(document: Any) -> list[Mapping[str, Any]]:
    findings: list[Mapping[str, Any]] = []
    if isinstance(document, dict):
        vulnerabilities = document.get("Vulnerabilities")
        if isinstance(vulnerabilities, list):
            findings.extend(item for item in vulnerabilities if isinstance(item, dict))
        for value in document.values():
            findings.extend(_vulnerabilities(value))
    elif isinstance(document, list):
        for value in document:
            findings.extend(_vulnerabilities(value))
    return findings


def _audit_inventory(root: Path, publication: Mapping[str, Any]) -> None:
    directory = root / EVIDENCE_DIR
    observed = {path.name for path in directory.iterdir() if path.is_file()}
    if observed != DIRECTORY_FILES:
        _fail("INVENTORY", f"evidence file set differs: {sorted(observed ^ DIRECTORY_FILES)}")
    retained = _mapping(publication.get("retained_files"), "retained_files")
    if set(retained) != ARTIFACT_FILES - {"publication.json"}:
        _fail("INVENTORY", "publication retained_files set is not closed")
    for name, identity_value in retained.items():
        identity = _mapping(identity_value, f"retained_files.{name}")
        if set(identity) != {"sha256", "size"}:
            _fail("INVENTORY", f"{name} identity fields differ")
        path = directory / name
        if path.stat().st_size != identity["size"] or _sha256(path) != identity["sha256"]:
            _fail("INVENTORY", f"{name} hash or size differs")
    if _sha256(directory / "publication.json") != PUBLICATION_SHA256:
        _fail("PUBLICATION", "publication record hash differs")
    if _sha256(root / HISTORICAL_WORKFLOW_PATH) != HISTORICAL_WORKFLOW_SHA256:
        _fail("PUBLISHER", "historical publisher workflow hash differs")


def _audit_publication(publication: Mapping[str, Any]) -> None:
    expected = {
        "artifact_role": "review_pending_dependency_evidence",
        "authorization_expires_at": "2026-08-21T22:43:36Z",
        "dependency_artifact_admitted": False,
        "digest": DIGEST,
        "image_publication_permitted": False,
        "reference": REFERENCE,
        "resident_admission_permitted": False,
        "run_attempt": RUN_ATTEMPT,
        "run_id": RUN_ID,
        "run_scoped_tag": f"run-{RUN_ID}-{RUN_ATTEMPT}",
        "runtime_reconciliation_permitted": False,
        "schema_version": 1,
        "source_identity": "provisionally-authorized-not-authenticated",
        "source_sha": SOURCE_SHA,
        "state": "dependency_snapshot_evidence_review_pending",
        "workflow_sha": SOURCE_SHA,
    }
    observed = {key: publication.get(key) for key in expected}
    if observed != expected:
        _fail("PUBLICATION", f"publication boundary differs: {observed!r}")


def _audit_semantics(root: Path) -> None:
    directory = root / EVIDENCE_DIR
    oci_manifest_path = directory / "oci-manifest.json"
    if f"sha256:{_sha256(oci_manifest_path)}" != DIGEST:
        _fail("OCI", "raw OCI manifest does not equal the published digest")
    oci_manifest = _mapping(_load_json(oci_manifest_path), "OCI manifest")
    expected_layers = [
        (
            "application/vnd.shirokuma.maven-dependency-manifest.v2+json",
            "sha256:c3753e3929cb93fa5e17a762e18f3e939416bc2130a5472dd7844ea5eb227728",
            2_996_705,
        ),
        (
            "application/vnd.shirokuma.maven-repository.v1.tar+gzip",
            "sha256:15a72b90cce23fd4795cefc916e9d5bfc7bcb5c0682ab17defe20bb9f8f60919",
            1_368_396_353,
        ),
        (
            "application/vnd.shirokuma.bun-dependency-manifest.v1+json",
            "sha256:6e7be3a404014f6f7ac7e4bc326c8d46f7d5822fcea1ac000219c17f1d23f421",
            17_312_735,
        ),
        (
            "application/vnd.shirokuma.bun-cache.v1.tar+gzip",
            "sha256:252eade2183bdf5a371f073752420c3a45f5ef8b1dacb08a4addea350389e3c2",
            128_423_777,
        ),
    ]
    observed_layers = [
        (layer.get("mediaType"), layer.get("digest"), layer.get("size"))
        for layer in oci_manifest.get("layers", [])
        if isinstance(layer, dict)
    ]
    if observed_layers != expected_layers:
        _fail("OCI", "ordered manifest layer closure differs")

    anonymous = _mapping(_load_json(directory / "anonymous-pull.json"), "anonymous")
    if (
        anonymous.get("reference") != REFERENCE
        or anonymous.get("registry_config") != "fresh-empty-no-credentials"
        or anonymous.get("result") != "passed"
        or any(
            anonymous.get(field) is not True
            for field in (
                "manifest_equal",
                "archive_equal",
                "bun_manifest_equal",
                "bun_archive_equal",
            )
        )
    ):
        _fail("ANONYMOUS_PULL", "exact no-credentials pull proof differs")
    if (
        anonymous.get("manifest_sha256")
        != _sha256(directory / "maven-dependency-manifest.json")
        or anonymous.get("bun_manifest_sha256")
        != _sha256(directory / "bun-dependency-manifest.json")
        or anonymous.get("archive_sha256")
        != expected_layers[1][1].removeprefix("sha256:")
        or anonymous.get("bun_archive_sha256")
        != expected_layers[3][1].removeprefix("sha256:")
    ):
        _fail("ANONYMOUS_PULL", "pulled layer identities differ from retained closure")

    reconstruction = _mapping(
        _load_json(directory / "independent-reconstruction.json"),
        "independent reconstruction",
    )
    required_true = (
        "archive_equal",
        "bun_cache_archive_equal",
        "bun_cache_manifest_equal",
        "complete_manifest_equal",
        "independent_fresh_repository",
        "same_allowlisted_repositories",
    )
    if reconstruction.get("result") != "passed" or any(
        reconstruction.get(field) is not True for field in required_true
    ):
        _fail("RECONSTRUCTION", "independent reconstruction proof differs")

    offline = _mapping(_load_json(directory / "offline-build.json"), "offline build")
    if (
        offline.get("result") != "passed"
        or offline.get("network") != "none"
        or offline.get("platform") != "linux/arm64"
        or offline.get("reproducible_build_comparison") != "equal"
        or offline.get("fresh_source_checkouts") != 2
        or offline.get("fresh_snapshot_extractions") != 2
        or offline.get("fresh_bun_cache_extractions") != 2
        or offline.get("maven_wrapper_used") is not False
    ):
        _fail("OFFLINE_BUILD", "network-none reproducible build proof differs")

    toolchain = _mapping(_load_json(directory / "toolchain.json"), "toolchain")
    if (
        toolchain.get("result") != "passed"
        or toolchain.get("runner_arch") != "ARM64"
        or toolchain.get("container_architecture") != "aarch64"
        or toolchain.get("native_execution") is not True
        or toolchain.get("qemu_binfmt_handlers") != []
    ):
        _fail("TOOLCHAIN", "native arm64 toolchain proof differs")

    slsa = _mapping(_load_json(directory / "slsa-provenance.json"), "SLSA")
    if (
        slsa.get("_type") != "https://in-toto.io/Statement/v1"
        or slsa.get("predicateType") != "https://slsa.dev/provenance/v1"
        or slsa.get("subject")
        != [
            {
                "name": "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies",
                "digest": {"sha256": DIGEST.removeprefix("sha256:")},
            }
        ]
    ):
        _fail("SLSA", "Statement/v1 subject or predicate differs")
    envelope = _mapping(
        _load_json(directory / "cosign-verify-attestation.json"),
        "verified attestation",
    )
    try:
        decoded = json.loads(base64.b64decode(envelope["payload"], validate=True))
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        _fail("SLSA", f"verified attestation payload is invalid: {error}")
    if decoded != slsa:
        _fail("SLSA", "verified attestation payload differs from retained statement")

    raw = _load_json(directory / "trivy-bun-vulnerability-raw.json")
    adjusted = _load_json(directory / "trivy-bun-vulnerability.json")
    maven = _load_json(directory / "trivy-vulnerability.json")
    raw_high = [
        finding
        for finding in _vulnerabilities(raw)
        if finding.get("Severity") == "HIGH"
    ]
    if len(raw_high) != 1 or raw_high[0].get("VulnerabilityID") != "GHSA-qwww-vcr4-c8h2":
        _fail("TRIVY", "raw Bun finding is not the exact reviewed High")
    raw_results = [
        (result.get("Target"), result.get("Packages"))
        for result in raw.get("Results", [])
        if isinstance(result, dict)
    ]
    adjusted_results = [
        (result.get("Target"), result.get("Packages"))
        for result in adjusted.get("Results", [])
        if isinstance(result, dict)
    ]
    if raw_results != adjusted_results or [
        (target, len(packages or [])) for target, packages in raw_results
    ] != [
        ("core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock", 299),
        ("core/trino-web-ui/src/main/resources/webapp/bun.lock", 470),
    ]:
        _fail("TRIVY", "raw and adjusted Bun package inventories differ")
    for label, document in (("adjusted Bun", adjusted), ("Maven", maven)):
        blocking = [
            finding
            for finding in _vulnerabilities(document)
            if finding.get("Severity") in {"HIGH", "CRITICAL"}
        ]
        if blocking:
            _fail("TRIVY", f"{label} report contains High/Critical findings")
    vex = _mapping(
        _load_json(
            directory / "react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json"
        ),
        "OpenVEX",
    )
    if "GHSA-qwww-vcr4-c8h2" not in json.dumps(vex, sort_keys=True):
        _fail("OPENVEX", "reviewed vulnerability identity is absent")
    for name in (
        "trino-bun-dependencies-483.cdx.json",
        "trino-maven-dependencies-483.cdx.json",
    ):
        sbom = _mapping(_load_json(directory / name), name)
        if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.7":
            _fail("SBOM", f"{name} is not CycloneDX 1.7")


def _audit_contract(root: Path) -> None:
    contract = _mapping(_load_json(root / CONTRACT_PATH), "contract")
    admission = _mapping(_load_json(root / ADMISSION_PATH), "admission")
    lifecycle = contract.get("lifecycle")
    if lifecycle != {
        "state": "dependency_snapshot_review_pending",
        "contract_only": False,
        "dependency_artifact_present": True,
        "publication_workflow_permitted": False,
        "image_publication_permitted": False,
        "resident_admission_permitted": False,
        "runtime_reconciliation_permitted": False,
    }:
        _fail("LIFECYCLE", f"unexpected lifecycle: {lifecycle!r}")
    snapshot = _mapping(contract.get("snapshot"), "snapshot")
    if (
        snapshot.get("state") != "evidence_review_pending_not_admitted"
        or snapshot.get("reference") != REFERENCE
        or snapshot.get("publication_evidence")
        != {
            "path": str(EVIDENCE_DIR / "publication.json"),
            "sha256": PUBLICATION_SHA256,
        }
    ):
        _fail("CONTRACT", "snapshot evidence pin differs")
    publication = _mapping(contract.get("publication"), "publication")
    if (
        publication.get("permitted") is not False
        or publication.get("workflow_present") is not False
        or publication.get("retired") is not True
        or publication.get("source_sha") != SOURCE_SHA
        or publication.get("run_id") != RUN_ID
        or publication.get("run_attempt") != RUN_ATTEMPT
    ):
        _fail("PUBLISHER", "retired publisher record differs")
    repository_state = _mapping(admission.get("repository_state"), "repository_state")
    if (
        repository_state.get("publication_workflow_permitted") is not False
        or repository_state.get("dependency_artifact_present") is not True
        or repository_state.get("resident_ledger_permitted") is not False
        or repository_state.get("runtime_manifests_permitted") is not False
        or admission.get("next_action", {}).get("phase")
        != "dependency_snapshot_evidence_review_pending"
    ):
        _fail("ADMISSION", "admission record crosses the evidence-review boundary")
    if (root / ACTIVE_WORKFLOW_PATH).exists():
        _fail("PUBLISHER", "retired write-capable publisher was reintroduced")


def _run_cosign(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        _fail("SIGSTORE", f"{' '.join(command[:3])} failed: {detail}")


def reverify_sigstore(root: Path) -> None:
    directory = root / EVIDENCE_DIR
    common = [
        "--certificate-identity",
        IDENTITY,
        "--certificate-oidc-issuer",
        ISSUER,
        "--certificate-github-workflow-repository",
        "TommyKammy/Shirokuma",
        "--certificate-github-workflow-ref",
        "refs/heads/main",
        "--certificate-github-workflow-sha",
        SOURCE_SHA,
        "--certificate-github-workflow-trigger",
        "push",
    ]
    _run_cosign(
        [
            "cosign",
            "verify-blob",
            "--bundle",
            str(directory / "cosign-signature-bundle.json"),
            *common,
            str(directory / "oci-manifest.json"),
        ]
    )
    _run_cosign(
        [
            "cosign",
            "verify-blob-attestation",
            "--bundle",
            str(directory / "cosign-provenance-bundle.json"),
            "--type",
            "slsaprovenance1",
            *common,
            str(directory / "oci-manifest.json"),
        ]
    )


def audit(root: Path, *, cryptographic: bool) -> None:
    publication = _mapping(
        _load_json(root / EVIDENCE_DIR / "publication.json"),
        "publication",
    )
    _audit_inventory(root, publication)
    _audit_publication(publication)
    _audit_semantics(root)
    _audit_contract(root)
    if cryptographic:
        reverify_sigstore(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("audit",))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        audit(args.root.resolve(), cryptographic=True)
    except EvidenceError as error:
        print(error, file=sys.stderr)
        return 1
    print("Trino 483 retained dependency evidence verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
