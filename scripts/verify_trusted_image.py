#!/usr/bin/env python3
"""Verify the closed-world SeaweedFS trusted-image admission contract."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


CONTRACT_PATH = Path("bootstrap/seaweedfs/v4.39/trusted-build-contract.json")
SOURCE_PATH = Path("bootstrap/seaweedfs/v4.39/source.json")
RELEASE_PATH = Path("bootstrap/seaweedfs/v4.39/release-evidence.json")
ADMISSION_PATH = Path("bootstrap/seaweedfs/v4.39/admission.json")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REMOTE_ACTION_RE = re.compile(r"^\s*uses:\s+[^\s@]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)


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
    _expect(source.get("schema_version") == 2, "SOURCE_SCHEMA", "expected schema 2")
    _expect(source_contract == CONTRACT_PATH.as_posix(), "SOURCE_CONTRACT", str(source_contract))
    _expect(source.get("component") == contract["component"], "SOURCE_COMPONENT", "mismatch")
    _expect(source.get("version") == contract["version"], "SOURCE_VERSION", "mismatch")

    container = contract["source"]["containerfile"]
    container_path = _safe_repo_path(root, container["path"], "CONTAINERFILE_PATH")
    _expect(container_path.is_file(), "CONTAINERFILE_MISSING", container["path"])
    actual_container_hash = _sha256(container_path)
    _expect(SHA256_RE.fullmatch(container["sha256"]) is not None, "CONTAINERFILE_HASH", "invalid")
    _expect(actual_container_hash == container["sha256"], "CONTAINERFILE_HASH", actual_container_hash)
    _expect(source.get("containerfile_sha256") == actual_container_hash, "SOURCE_CONTAINERFILE_HASH", "mismatch")
    _expect(
        source.get("build_inputs", {}).get("dockerfile_frontend") == container["frontend"],
        "FRONTEND_PIN",
        "source and contract differ",
    )

    toolchain = contract.get("toolchain", {})
    expected_tools = {"buildx", "buildkit", "syft", "trivy", "cosign", "crane"}
    _expect(set(toolchain) == expected_tools, "TOOLCHAIN_CLOSED_WORLD", repr(sorted(toolchain)))
    for name, record in toolchain.items():
        version = record.get("version", "")
        _expect(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version) is not None, "TOOL_VERSION", name)
    for tool, field in (("buildx", "linux_arm64_sha256"), ("crane", "linux_arm64_archive_sha256")):
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
    workflow_path = _safe_repo_path(root, workflow_record["path"], "WORKFLOW_PATH")
    workflow = workflow_path.read_text(encoding="utf-8")
    _expect(f"runs-on: {workflow_record['runner']}" in workflow, "WORKFLOW_RUNNER", workflow_record["runner"])
    _expect("imjasonh/setup-crane@" not in workflow, "CRANE_UNVERIFIED_SETUP", "setup-crane is forbidden")
    _expect("docker/setup-buildx-action@" not in workflow, "BUILDX_UNVERIFIED_SETUP", "setup-buildx is forbidden")

    uses_lines = [line for line in workflow.splitlines() if line.lstrip().startswith("uses:")]
    for line in uses_lines:
        _expect(REMOTE_ACTION_RE.fullmatch(line) is not None, "ACTION_NOT_SHA_PINNED", line.strip())

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
        "--signer-workflow",
        "cosign-signature-bundle.json",
        "toolchain.json",
        "promotion-evidence.json",
    )
    for literal in required_literals:
        _expect(literal in workflow, "WORKFLOW_CONTRACT_LITERAL", literal)

    positions: List[int] = []
    for name in workflow_record["gate_order"]:
        marker = f"- name: {name}"
        _expect(marker in workflow, "WORKFLOW_GATE_MISSING", name)
        positions.append(workflow.index(marker))
    _expect(positions == sorted(positions), "WORKFLOW_GATE_ORDER", "gate order differs from contract")
    _expect("needs: verify" in workflow, "PROMOTION_DEPENDENCY", "promote job must need verify")

    buildx_install = workflow.index("- name: Install and verify pinned Buildx and BuildKit without credentials")
    quarantine_login = workflow.index("- name: Log in to GHCR for the quarantine push")
    crane_install = workflow.index("- name: Install and verify pinned Crane without credentials")
    promotion_login = workflow.index("- name: Log in to GHCR for trusted-tag promotion")
    _expect(buildx_install < quarantine_login, "BUILDX_CREDENTIAL_BOUNDARY", "install must precede login")
    _expect(crane_install < promotion_login, "CRANE_CREDENTIAL_BOUNDARY", "install must precede login")

    admission_contract = contract["admission"]
    _expect(
        admission.get("assessment", {}).get("admission") == admission_contract["artifact"],
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
    release: Dict[str, Any],
    verification_path: Path,
    bundle_path: Path,
    manifest_path: Path,
    rekor_path: Path,
) -> None:
    verification = _load_json(verification_path)
    _expect(verification.get("schema_version") == 1, "COSIGN_VERIFY_SCHEMA", "expected schema 1")
    _expect(verification.get("reference") == release["reference"], "COSIGN_REFERENCE", "mismatch")
    constraints = verification.get("certificate_constraints", {})
    builder = release["builder"]
    for field, expected in (
        ("issuer", release["issuer"]),
        ("identity", release["identity"]),
        ("github_workflow_sha", builder["workflow_sha"]),
        ("github_workflow_ref", builder["ref"]),
    ):
        _expect(constraints.get(field) == expected, "COSIGN_CERTIFICATE_CONSTRAINT", field)
    payloads = verification.get("verified_payloads")
    _expect(isinstance(payloads, list) and payloads, "COSIGN_VERIFIED_PAYLOAD", "empty")
    digest = _digest_hex(release["reference"])
    _expect(
        any(
            payload.get("critical", {}).get("image", {}).get("docker-manifest-digest")
            == f"sha256:{digest}"
            for payload in payloads
        ),
        "COSIGN_VERIFIED_PAYLOAD",
        "digest mismatch",
    )
    _expect(verification.get("bundle_verified_offline") is True, "COSIGN_OFFLINE_VERIFY", "missing")
    _expect(verification.get("registry_signature_verified") is True, "COSIGN_REGISTRY_VERIFY", "missing")
    _expect(_sha256(manifest_path) == digest, "COSIGN_MANIFEST_DIGEST", "mismatch")

    bundle = _load_json(bundle_path)
    _expect(bundle.get("mediaType") == "application/vnd.dev.sigstore.bundle.v0.3+json", "COSIGN_BUNDLE_MEDIA", "v0.3 required")
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
    _expect(statement.get("predicateType") == "https://sigstore.dev/cosign/sign/v1", "COSIGN_PREDICATE", "unexpected")
    _expect(
        any(subject.get("digest", {}).get("sha256") == digest for subject in statement.get("subject", [])),
        "COSIGN_SUBJECT",
        "digest mismatch",
    )
    recorded_rekor = verification.get("rekor_entries")
    _expect(isinstance(recorded_rekor, list) and len(recorded_rekor) == len(tlog_entries), "COSIGN_REKOR_RECORD", "mismatch")
    for recorded, raw in zip(recorded_rekor, tlog_entries):
        _expect(str(recorded.get("log_index")) == str(raw["logIndex"]), "COSIGN_REKOR_RECORD", "log index")
        _expect(str(recorded.get("integrated_time")) == str(raw["integratedTime"]), "COSIGN_REKOR_RECORD", "time")
        _expect(recorded.get("log_id") == raw["logId"]["keyId"], "COSIGN_REKOR_RECORD", "log id")

    rekor_response = _load_json(rekor_path)
    _expect(isinstance(rekor_response, dict) and len(rekor_response) == 1, "COSIGN_REKOR_RESPONSE", "expected one UUID")
    uuid, api_entry = next(iter(rekor_response.items()))
    raw = tlog_entries[0]
    _expect(re.fullmatch(r"[0-9a-f]{80}", uuid) is not None, "COSIGN_REKOR_UUID", uuid)
    _expect(str(api_entry.get("logIndex")) == str(raw["logIndex"]), "COSIGN_REKOR_RESPONSE", "log index")
    _expect(str(api_entry.get("integratedTime")) == str(raw["integratedTime"]), "COSIGN_REKOR_RESPONSE", "time")
    _expect(api_entry.get("body") == raw.get("canonicalizedBody"), "COSIGN_REKOR_RESPONSE", "body")
    try:
        bundle_log_id_hex = base64.b64decode(raw["logId"]["keyId"], validate=True).hex()
    except ValueError as exc:
        _fail("COSIGN_REKOR_LOG_ID", str(exc))
    _expect(api_entry.get("logID") == bundle_log_id_hex, "COSIGN_REKOR_RESPONSE", "log id")
    _expect(recorded_rekor[0].get("uuid") == uuid, "COSIGN_REKOR_RECORD", "uuid")


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
    retained_bundle_keys = {
        json.dumps(bundle, sort_keys=True, separators=(",", ":"))
        for bundle in retained_bundles
    }
    builder = release["builder"]
    repository = builder["repository"]
    workflow_path = builder["workflow"]
    workflow_ref = builder["ref"]
    workflow_sha = builder["workflow_sha"]
    identity = f"https://github.com/{repository}/{workflow_path}@{workflow_ref}"
    repository_url = f"https://github.com/{repository}"
    invocation = f"{repository_url}/actions/runs/{builder['run_id']}/attempts/{builder['run_attempt']}"
    digest = _digest_hex(release["reference"])

    matches = 0
    for record in records:
        certificate = record.get("verificationResult", {}).get("signature", {}).get("certificate", {})
        statement = _slsa_statement(record)
        predicate = statement.get("predicate", {})
        definition = predicate.get("buildDefinition", {})
        workflow = definition.get("externalParameters", {}).get("workflow", {})
        details = predicate.get("runDetails", {})
        conditions = (
            statement.get("predicateType") == "https://slsa.dev/provenance/v1",
            certificate.get("issuer") == release["issuer"],
            certificate.get("subjectAlternativeName") == identity,
            certificate.get("githubWorkflowRepository") == repository,
            certificate.get("githubWorkflowRef") == workflow_ref,
            certificate.get("githubWorkflowSHA") == workflow_sha,
            certificate.get("buildSignerURI") == identity,
            certificate.get("buildSignerDigest") == workflow_sha,
            certificate.get("buildConfigURI") == identity,
            certificate.get("buildConfigDigest") == workflow_sha,
            certificate.get("runInvocationURI") == invocation,
            workflow.get("repository") == repository_url,
            workflow.get("path") == workflow_path,
            workflow.get("ref") == workflow_ref,
            details.get("builder", {}).get("id") == identity,
            details.get("metadata", {}).get("invocationId") == invocation,
            any(subject.get("digest", {}).get("sha256") == digest for subject in statement.get("subject", [])),
        )
        if all(conditions):
            matches += 1
            _expect(
                json.dumps(
                    record["attestation"]["bundle"],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                in retained_bundle_keys,
                "SLSA_BUNDLES",
                "matching bundle was not retained",
            )
    _expect(matches >= 1, "SLSA_WORKFLOW_IDENTITY", "no exact workflow match")


def _validate_toolchain(contract: Dict[str, Any], path: Path) -> None:
    actual = _load_json(path)
    _expect(actual.get("schema_version") == 1, "TOOLCHAIN_SCHEMA", "expected schema 1")
    expected = contract["toolchain"]
    _expect(set(actual.get("tools", {})) == set(expected), "TOOLCHAIN_CLOSED_WORLD", "actual tool set differs")
    for name, pinned in expected.items():
        record = actual["tools"][name]
        _expect(record.get("version") == pinned["version"], "TOOLCHAIN_VERSION", name)
    _expect(
        actual["tools"]["buildx"].get("binary_sha256") == expected["buildx"]["linux_arm64_sha256"],
        "BUILDX_BINARY_HASH",
        "mismatch",
    )
    _expect(
        actual["tools"]["buildkit"].get("image") == expected["buildkit"]["image"],
        "BUILDKIT_IMAGE_PIN",
        "runtime mismatch",
    )
    _expect(
        actual.get("credential_boundary")
        == "standalone archives verified before credentialed execution",
        "TOOL_CREDENTIAL_BOUNDARY",
        "missing",
    )


def _validate_promotion(release: Dict[str, Any], contract: Dict[str, Any], path: Path) -> None:
    promotion = _load_json(path)
    _expect(promotion.get("schema_version") == 1, "PROMOTION_SCHEMA", "expected schema 1")
    _expect(promotion.get("status") == "verified", "PROMOTION_STATUS", "not verified")
    _expect(promotion.get("reference") == release["reference"], "PROMOTION_REFERENCE", "mismatch")
    _expect(promotion.get("trusted_tag_digest") == release["digest"], "PROMOTION_DIGEST", "mismatch")
    crane = contract["toolchain"]["crane"]
    _expect(promotion.get("tool", {}).get("version") == crane["version"], "PROMOTION_TOOL", "version")
    _expect(
        promotion.get("tool", {}).get("archive_sha256") == crane["linux_arm64_archive_sha256"],
        "PROMOTION_TOOL",
        "archive hash",
    )
    _expect(promotion.get("tool", {}).get("verified_before_registry_login") is True, "PROMOTION_CREDENTIAL_BOUNDARY", "missing")


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
    release = _load_json(release_path)
    _expect(release.get("schema_version") == 2, "RELEASE_SCHEMA", "expected schema 2")
    _expect(release.get("component") == contract["component"], "RELEASE_COMPONENT", "mismatch")
    _expect(release.get("version") == contract["version"], "RELEASE_VERSION", "mismatch")
    _expect(release.get("platform") == contract["platform"], "RELEASE_PLATFORM", "mismatch")
    _expect(release.get("digest") == "sha256:" + _digest_hex(release["reference"]), "RELEASE_DIGEST", "mismatch")
    _expect(release.get("contract", {}).get("path") == CONTRACT_PATH.as_posix(), "RELEASE_CONTRACT", "path")
    _expect(release.get("contract", {}).get("sha256") == _sha256(root / CONTRACT_PATH), "RELEASE_CONTRACT", "hash")

    artifacts = release.get("artifacts", {})
    required: List[str] = list(contract["evidence"]["candidate_required"])
    if require_promotion:
        required.extend(contract["evidence"]["promotion_required"])
    _expect(set(required).issubset(artifacts), "EVIDENCE_SET", "required artifact missing")
    paths: Dict[str, Path] = {}
    for name in required:
        metadata = artifacts[name]
        path = _artifact_file(root, evidence_dir, name, metadata)
        _expect(path.is_file(), "EVIDENCE_MISSING", name)
        expected_hash = metadata.get("sha256", "")
        _expect(SHA256_RE.fullmatch(expected_hash) is not None, "EVIDENCE_HASH", name)
        _expect(_sha256(path) == expected_hash, "EVIDENCE_HASH", name)
        paths[name] = path

    _validate_cosign(
        release,
        paths["cosign-verify.json"],
        paths["cosign-signature-bundle.json"],
        paths["image-manifest.json"],
        paths["rekor-entry.json"],
    )
    _validate_slsa(
        release,
        paths["slsa-verify.json"],
        paths["slsa-bundles.jsonl"],
    )
    _validate_toolchain(contract, paths["toolchain.json"])
    smoke = _load_json(paths["runtime-smoke.json"])
    _expect(smoke.get("digest") == release["digest"], "RUNTIME_SMOKE_DIGEST", "mismatch")
    _expect(smoke.get("result") == "passed", "RUNTIME_SMOKE_RESULT", "not passed")
    if require_promotion:
        _validate_promotion(release, contract, paths["promotion-evidence.json"])
        _expect(release.get("promotion", {}).get("status") == "verified", "PROMOTION_STATUS", "release record")
    else:
        _expect(release.get("promotion", {}).get("status") == "pending", "PROMOTION_STATUS", "candidate must be pending")
    return release


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("contract", "candidate", "final", "repository"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-dir", type=Path)
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
        else:
            validate_release_bundle(args.root, require_promotion=True)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"trusted-image {args.mode} verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
