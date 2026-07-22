#!/usr/bin/env python3
"""Fail-closed static audit for the credential-safe Polaris runtime activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


CONTRACT = Path("security/polaris-runtime-activation.json")
POLARIS_IMAGE = (
    "ghcr.io/tommykammy/shirokuma-polaris@"
    "sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e"
)
POSTGRES_IMAGE = (
    "cgr.dev/chainguard/postgres@"
    "sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8"
)
ADMIN_IMAGE = (
    "ghcr.io/tommykammy/shirokuma-polaris-admin@"
    "sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd"
)
EXPECTED_FLUX_ORDER = [
    "shirokuma-object-storage",
    "shirokuma-catalog-database",
    "shirokuma-catalog-bootstrap",
    "shirokuma-catalog",
]
EXPECTED_SECRET_REFS = {
    "polaris-postgresql-credentials": ["database", "password", "username"],
    "polaris-root-credentials": ["credentials.json"],
    "seaweedfs-s3-application-credentials": [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "S3_REGION",
    ],
}
EXPECTED_POLARIS_STORAGE_ENV = {
    "AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION": "S3_REGION",
}
EXPECTED_SECRET_DATA_KEYS = {
    "polaris-postgresql-credentials": ["database", "password", "username"],
    "polaris-root-credentials": [
        "client_id",
        "client_secret",
        "credentials.json",
        "realm",
    ],
}


class RuntimeContractError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise RuntimeContractError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load_json(
    path: Path,
    code: str = "RUNTIME_CONTRACT",
) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail(code, f"cannot read {path}: {error}")
    _expect(isinstance(value, dict), code, f"{path} must contain an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_map(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    manifests = contract.get("manifests")
    _expect(isinstance(manifests, dict), "RUNTIME_CONTRACT", "manifests must be an object")
    return manifests


def _documentation_map(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    documentation = contract.get("documentation")
    _expect(
        isinstance(documentation, dict),
        "RUNTIME_CONTRACT",
        "documentation must be an object",
    )
    return documentation


def _tooling_map(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    tooling = contract.get("tooling")
    _expect(isinstance(tooling, dict), "RUNTIME_CONTRACT", "tooling must be an object")
    return tooling


def _audit_contract(root: Path) -> Mapping[str, Any]:
    path = root / CONTRACT
    _expect(path.is_file() and not path.is_symlink(), "RUNTIME_CONTRACT", f"missing {CONTRACT}")
    contract = _load_json(path)
    expected_keys = {
        "schema_version",
        "issue",
        "state",
        "images",
        "flux_order",
        "secrets",
        "admin_bootstrap",
        "credential_generation",
        "documentation",
        "tooling",
        "manifests",
        "live_acceptance",
    }
    _expect(set(contract) == expected_keys, "RUNTIME_CONTRACT", "contract key set changed")
    _expect(contract.get("schema_version") == 2, "RUNTIME_CONTRACT", "schema_version must be 2")
    _expect(contract.get("issue") == 61, "RUNTIME_CONTRACT", "issue must be 61")
    _expect(
        contract.get("state")
        in {"runtime_acceptance_pending", "runtime_accepted"},
        "RUNTIME_CONTRACT",
        "state must be runtime_acceptance_pending or runtime_accepted",
    )
    _expect(
        contract.get("images")
        == {
            "polaris": POLARIS_IMAGE,
            "postgresql": POSTGRES_IMAGE,
            "polaris-admin": ADMIN_IMAGE,
        },
        "RUNTIME_IMAGES",
        "runtime image set changed",
    )
    _expect(contract.get("flux_order") == EXPECTED_FLUX_ORDER, "RUNTIME_FLUX", "Flux dependency order changed")
    _expect(
        contract.get("secrets")
        == {
            "provisioner": "OpenTofu",
            "manifests_contain_material": False,
            "references": EXPECTED_SECRET_REFS,
        },
        "RUNTIME_SECRET",
        "external Secret boundary changed",
    )
    _expect(
        contract.get("admin_bootstrap")
        == {
            "command": [
                "bootstrap",
                "--credentials-file=/var/run/secrets/shirokuma/polaris/credentials.json",
            ],
            "credential_output_permitted": False,
        },
        "RUNTIME_ADMIN",
        "Admin bootstrap boundary changed",
    )
    _expect(
        contract.get("credential_generation")
        == {
            "source": "deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml",
            "in_place_rotation_permitted": False,
            "requires_catalog_rebuild": True,
        },
        "RUNTIME_GENERATION",
        "credential generation boundary changed",
    )
    _expect(
        isinstance(contract.get("live_acceptance"), dict),
        "RUNTIME_ACCEPTANCE",
        "live acceptance must be an object",
    )
    return contract


def _audit_manifests(root: Path, contract: Mapping[str, Any]) -> dict[str, str]:
    manifests = _manifest_map(contract)
    expected_paths = {
        "deploy/gitops/clusters/local-lite/kustomization.yaml",
        "deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml",
        "deploy/gitops/clusters/local-lite/catalog-database.yaml",
        "deploy/gitops/clusters/local-lite/catalog-bootstrap.yaml",
        "deploy/gitops/clusters/local-lite/catalog.yaml",
        "deploy/gitops/catalog/database/kustomization.yaml",
        "deploy/gitops/catalog/database/networkpolicy.yaml",
        "deploy/gitops/catalog/database/service.yaml",
        "deploy/gitops/catalog/database/statefulset.yaml",
        "deploy/catalog/bootstrap/kustomization.yaml",
        "deploy/catalog/bootstrap/networkpolicy.yaml",
        "deploy/catalog/bootstrap/job.yaml",
        "deploy/gitops/catalog/server/kustomization.yaml",
        "deploy/gitops/catalog/server/networkpolicy.yaml",
        "deploy/gitops/catalog/server/service.yaml",
        "deploy/gitops/catalog/server/deployment.yaml",
        "deploy/gitops/object-storage/statefulset.yaml",
        "opentofu/dev/catalog.tf",
    }
    _expect(set(manifests) == expected_paths, "RUNTIME_MANIFEST", "activation file set changed")
    texts: dict[str, str] = {}
    for relative, expected_digest in manifests.items():
        _expect(
            isinstance(relative, str)
            and isinstance(expected_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_digest) is not None,
            "RUNTIME_MANIFEST",
            f"invalid manifest record: {relative!r}",
        )
        path = root / relative
        _expect(path.is_file() and not path.is_symlink(), "RUNTIME_MANIFEST", f"missing regular file: {relative}")
        _expect(_sha256(path) == expected_digest, "RUNTIME_MANIFEST", f"hash mismatch: {relative}")
        texts[relative] = path.read_text(encoding="utf-8")
    return texts


def _audit_documentation(
    root: Path, contract: Mapping[str, Any]
) -> dict[str, str]:
    documentation = _documentation_map(contract)
    expected = {
        "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md",
        "docs/design/08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo.md",
    }
    _expect(
        set(documentation) == expected,
        "RUNTIME_DOCUMENTATION",
        "runtime recovery documentation set changed",
    )
    texts: dict[str, str] = {}
    for relative, expected_digest in documentation.items():
        _expect(
            isinstance(expected_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_digest) is not None,
            "RUNTIME_DOCUMENTATION",
            f"invalid documentation record: {relative!r}",
        )
        path = root / relative
        _expect(
            path.is_file() and not path.is_symlink(),
            "RUNTIME_DOCUMENTATION",
            f"missing regular file: {relative}",
        )
        _expect(
            _sha256(path) == expected_digest,
            "RUNTIME_DOCUMENTATION",
            f"hash mismatch: {relative}",
        )
        texts[relative] = path.read_text(encoding="utf-8")
    return texts


def _audit_tooling(root: Path, contract: Mapping[str, Any]) -> None:
    tooling = _tooling_map(contract)
    expected = {
        "scripts/polaris_runtime_acceptance.py",
        "tests/test_polaris_runtime_acceptance.py",
    }
    _expect(set(tooling) == expected, "RUNTIME_TOOLING", "acceptance tooling set changed")
    for relative, expected_digest in tooling.items():
        _expect(
            isinstance(relative, str)
            and isinstance(expected_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_digest) is not None,
            "RUNTIME_TOOLING",
            f"invalid tooling record: {relative!r}",
        )
        path = root / relative
        _expect(
            path.is_file() and not path.is_symlink(),
            "RUNTIME_TOOLING",
            f"missing regular file: {relative}",
        )
        _expect(
            _sha256(path) == expected_digest,
            "RUNTIME_TOOLING",
            f"hash mismatch: {relative}",
        )


def _git_blob_sha256(root: Path, revision: str, relative: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{relative}"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        _fail(
            "RUNTIME_ACCEPTANCE",
            f"cannot read accepted revision binding for {relative}: {error}",
        )
    return hashlib.sha256(result.stdout).hexdigest()


def _audit_accepted_revision_binding(
    root: Path,
    contract: Mapping[str, Any],
    repository_revision: str,
) -> None:
    for records in (
        _manifest_map(contract),
        _documentation_map(contract),
        _tooling_map(contract),
    ):
        for relative, expected_digest in records.items():
            _expect(
                _git_blob_sha256(root, repository_revision, relative)
                == expected_digest,
                "RUNTIME_ACCEPTANCE",
                "accepted revision does not contain the contracted desired state: "
                f"{relative}",
            )


def _audit_iceberg_acceptance_receipt(
    path: Path,
    contract: Mapping[str, Any],
    repository_revision: str,
) -> None:
    receipt = _load_json(path, "RUNTIME_ACCEPTANCE")
    _expect(
        set(receipt)
        == {
            "schema_version",
            "kind",
            "issue",
            "captured_at",
            "scope",
            "cluster",
            "flux",
            "image",
            "initial",
            "rerun_after_polaris_restart",
            "storage_inventory_after_rerun",
            "capacity",
            "assertions",
            "secrets",
        },
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance receipt key set changed",
    )
    _expect(
        type(receipt.get("schema_version")) is int
        and receipt.get("schema_version") == 1
        and receipt.get("kind") == "iceberg_table_bootstrap_runtime_acceptance"
        and type(receipt.get("issue")) is int
        and receipt.get("issue") == 62
        and receipt.get("scope") == "non-production local-lite",
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance receipt identity is invalid",
    )
    captured_at = receipt.get("captured_at")
    try:
        parsed_captured_at = datetime.strptime(
            captured_at if isinstance(captured_at, str) else "",
            "%Y-%m-%dT%H:%M:%SZ",
        )
    except ValueError:
        parsed_captured_at = None
    _expect(
        parsed_captured_at is not None
        and parsed_captured_at.strftime("%Y-%m-%dT%H:%M:%SZ") == captured_at,
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance receipt timestamp is invalid",
    )

    expected_revision = f"main@sha1:{repository_revision}"
    _expect(
        receipt.get("cluster")
        == {
            "kubernetes_context": "colima-mac-studio-solo",
            "namespace": "shirokuma-dev",
            "repository_revision": repository_revision,
            "flux_revision": expected_revision,
        },
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance cluster or revision binding is invalid",
    )

    flux = receipt.get("flux")
    _expect(
        isinstance(flux, dict) and set(flux) == {"kustomizations"},
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance Flux evidence key set changed",
    )
    kustomizations = flux.get("kustomizations") if isinstance(flux, dict) else None
    _expect(
        isinstance(kustomizations, list)
        and kustomizations
        == [
            {
                "name": "shirokuma-catalog",
                "ready": True,
                "revision": expected_revision,
            },
            {
                "name": "shirokuma-iceberg-bootstrap",
                "ready": True,
                "revision": expected_revision,
            },
        ],
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance Flux Ready evidence is invalid",
    )

    images = contract.get("images")
    image_reference = images.get("polaris") if isinstance(images, dict) else None
    _expect(
        image_reference == POLARIS_IMAGE
        and receipt.get("image")
        == {
            "reference": image_reference,
            "runtime_image_id": f"docker-pullable://{image_reference}",
        },
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance runtime image evidence is invalid",
    )

    run_keys = {
        "polaris_pod_uid",
        "polaris_restart_count",
        "job_uid",
        "job_pod_uid",
        "summary",
        "summary_canonical_sha256",
    }
    summary_keys = {
        "catalog",
        "namespace",
        "table",
        "result",
        "created",
        "snapshot_id",
        "data_files",
        "rows",
        "credential_material_retained",
    }
    runs: list[Mapping[str, Any]] = []
    for key, expected_created in (
        ("initial", True),
        ("rerun_after_polaris_restart", False),
    ):
        run = receipt.get(key)
        _expect(
            isinstance(run, dict) and set(run) == run_keys,
            "RUNTIME_ACCEPTANCE",
            f"Iceberg acceptance {key} key set changed",
        )
        summary = run.get("summary") if isinstance(run, dict) else None
        _expect(
            isinstance(summary, dict)
            and set(summary) == summary_keys
            and summary.get("catalog") == "shirokuma_l1"
            and summary.get("namespace") == "smoke"
            and summary.get("table") == "fixture_v1"
            and summary.get("result") == "passed"
            and summary.get("created") is expected_created
            and isinstance(summary.get("snapshot_id"), str)
            and summary["snapshot_id"].isdigit()
            and int(summary["snapshot_id"]) > 0
            and type(summary.get("data_files")) is int
            and summary.get("data_files") == 1
            and type(summary.get("rows")) is int
            and summary.get("rows") == 2
            and summary.get("credential_material_retained") is False,
            "RUNTIME_ACCEPTANCE",
            f"Iceberg acceptance {key} summary is invalid",
        )
        canonical = json.dumps(
            summary,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        _expect(
            isinstance(run.get("summary_canonical_sha256"), str)
            and run.get("summary_canonical_sha256")
            == hashlib.sha256(canonical).hexdigest(),
            "RUNTIME_ACCEPTANCE",
            f"Iceberg acceptance {key} summary digest is invalid",
        )
        _expect(
            type(run.get("polaris_restart_count")) is int
            and run.get("polaris_restart_count") == 0
            and all(
                isinstance(run.get(uid_key), str)
                and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                    run[uid_key],
                )
                is not None
                for uid_key in ("polaris_pod_uid", "job_uid", "job_pod_uid")
            ),
            "RUNTIME_ACCEPTANCE",
            f"Iceberg acceptance {key} workload identity is invalid",
        )
        runs.append(run)

    initial, rerun = runs
    initial_summary = initial["summary"]
    rerun_summary = rerun["summary"]
    _expect(
        initial["polaris_pod_uid"] != rerun["polaris_pod_uid"]
        and initial["job_uid"] != rerun["job_uid"]
        and initial["job_pod_uid"] != rerun["job_pod_uid"]
        and initial_summary["snapshot_id"] == rerun_summary["snapshot_id"],
        "RUNTIME_ACCEPTANCE",
        "Iceberg restart and idempotence evidence is invalid",
    )

    storage = receipt.get("storage_inventory_after_rerun")
    _expect(
        isinstance(storage, dict)
        and set(storage)
        == {
            "bucket",
            "prefix",
            "object_count",
            "total_bytes",
            "maximum_object_count",
            "maximum_total_bytes",
        }
        and storage.get("bucket") == "shirokuma-lakehouse"
        and storage.get("prefix") == "l1/"
        and type(storage.get("object_count")) is int
        and 0 < storage["object_count"] <= 8
        and type(storage.get("total_bytes")) is int
        and 0 < storage["total_bytes"] <= 1_048_576
        and type(storage.get("maximum_object_count")) is int
        and storage.get("maximum_object_count") == 8
        and type(storage.get("maximum_total_bytes")) is int
        and storage.get("maximum_total_bytes") == 1_048_576,
        "RUNTIME_ACCEPTANCE",
        "Iceberg storage inventory evidence is invalid",
    )

    capacity = receipt.get("capacity")
    _expect(
        isinstance(capacity, dict)
        and set(capacity)
        == {
            "minimum_available_kib",
            "host_available_kib",
            "colima_available_kib",
        }
        and type(capacity.get("minimum_available_kib")) is int
        and capacity.get("minimum_available_kib") == 131_072
        and type(capacity.get("host_available_kib")) is int
        and capacity["host_available_kib"] >= 131_072
        and type(capacity.get("colima_available_kib")) is int
        and capacity["colima_available_kib"] >= 131_072,
        "RUNTIME_ACCEPTANCE",
        "Iceberg capacity evidence is invalid",
    )

    expected_assertions = {
        "polaris_pod_uid_changed": initial["polaris_pod_uid"]
        != rerun["polaris_pod_uid"],
        "job_uid_changed": initial["job_uid"] != rerun["job_uid"],
        "snapshot_id_unchanged": initial_summary["snapshot_id"]
        == rerun_summary["snapshot_id"],
        "idempotent_rerun": initial_summary["created"] is True
        and rerun_summary["created"] is False,
        "storage_guard_passed": storage["object_count"]
        <= storage["maximum_object_count"]
        and storage["total_bytes"] <= storage["maximum_total_bytes"],
        "capacity_guard_passed": capacity["host_available_kib"]
        >= capacity["minimum_available_kib"]
        and capacity["colima_available_kib"] >= capacity["minimum_available_kib"],
    }
    _expect(
        receipt.get("assertions") == expected_assertions
        and all(expected_assertions.values()),
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance assertions are invalid",
    )
    _expect(
        receipt.get("secrets")
        == {
            "credential_material_retained": False,
            "pod_spec_retained": False,
            "environment_retained": False,
        },
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance secret boundary changed",
    )


def _audit_live_acceptance(root: Path, contract: Mapping[str, Any]) -> None:
    live = contract.get("live_acceptance")
    _expect(isinstance(live, dict), "RUNTIME_ACCEPTANCE", "live acceptance is missing")
    required = [
        "flux_ready",
        "catalog_create_list_read",
        "backup_restore",
        "rollback_teardown",
    ]
    if contract.get("state") == "runtime_acceptance_pending":
        _expect(
            live == {"complete": False, "required": required},
            "RUNTIME_ACCEPTANCE",
            "changed desired state must remain explicitly pending without a stale receipt binding",
        )
        return
    _expect(
        set(live)
        == {
            "complete",
            "receipt",
            "receipt_sha256",
            "additional_receipts",
            "required",
        },
        "RUNTIME_ACCEPTANCE",
        "live acceptance key set changed",
    )
    _expect(live.get("complete") is True, "RUNTIME_ACCEPTANCE", "live acceptance is incomplete")
    _expect(
        live.get("required") == required,
        "RUNTIME_ACCEPTANCE",
        "live acceptance requirements changed",
    )
    relative = live.get("receipt")
    expected_digest = live.get("receipt_sha256")
    _expect(
        relative == "security/evidence/polaris-runtime-acceptance.json"
        and isinstance(expected_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_digest) is not None,
        "RUNTIME_ACCEPTANCE",
        "live acceptance receipt binding is invalid",
    )
    path = root / relative
    _expect(
        path.is_file() and not path.is_symlink(),
        "RUNTIME_ACCEPTANCE",
        "live acceptance receipt is missing",
    )
    _expect(
        _sha256(path) == expected_digest,
        "RUNTIME_ACCEPTANCE",
        "live acceptance receipt hash mismatch",
    )
    additional = live.get("additional_receipts")
    _expect(
        isinstance(additional, list)
        and len(additional) == 1
        and isinstance(additional[0], dict)
        and set(additional[0]) == {"receipt", "receipt_sha256"},
        "RUNTIME_ACCEPTANCE",
        "additional acceptance receipt binding is invalid",
    )
    additional_relative = additional[0].get("receipt")
    additional_digest = additional[0].get("receipt_sha256")
    _expect(
        additional_relative
        == "security/evidence/iceberg-table-bootstrap-runtime-acceptance.json"
        and isinstance(additional_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", additional_digest) is not None,
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance receipt binding is invalid",
    )
    additional_path = root / additional_relative
    _expect(
        additional_path.is_file() and not additional_path.is_symlink(),
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance receipt is missing",
    )
    _expect(
        _sha256(additional_path) == additional_digest,
        "RUNTIME_ACCEPTANCE",
        "Iceberg acceptance receipt hash mismatch",
    )
    receipt = _load_json(path)
    _expect(
        set(receipt)
        == {
            "schema_version",
            "kind",
            "issue",
            "acceptance_tool_sha256",
            "captured_at",
            "repository_revision",
            "cluster",
            "readiness",
            "catalog_api_smoke",
            "backup_restore",
            "rollback_teardown",
            "secrets",
        },
        "RUNTIME_ACCEPTANCE",
        "receipt key set changed",
    )
    _expect(
        receipt.get("schema_version") == 1
        and receipt.get("kind") == "shirokuma-polaris-runtime-acceptance"
        and receipt.get("issue") == 61,
        "RUNTIME_ACCEPTANCE",
        "receipt identity is invalid",
    )
    _expect(
        receipt.get("acceptance_tool_sha256")
        == _tooling_map(contract)["scripts/polaris_runtime_acceptance.py"],
        "RUNTIME_ACCEPTANCE",
        "receipt was not produced by the bound acceptance tool",
    )
    repository_revision = receipt.get("repository_revision")
    _expect(
        isinstance(repository_revision, str)
        and re.fullmatch(r"[0-9a-f]{40}", repository_revision) is not None,
        "RUNTIME_ACCEPTANCE",
        "receipt repository revision is invalid",
    )
    _audit_accepted_revision_binding(root, contract, repository_revision)
    _audit_iceberg_acceptance_receipt(
        additional_path,
        contract,
        repository_revision,
    )
    cluster = receipt.get("cluster")
    _expect(
        cluster
        == {
            "context": "colima-mac-studio-solo",
            "namespace": "shirokuma-dev",
            "production_claim": False,
            "profile": "local-lite",
        },
        "RUNTIME_ACCEPTANCE",
        "receipt cluster boundary changed",
    )
    readiness = receipt.get("readiness")
    _expect(isinstance(readiness, dict), "RUNTIME_ACCEPTANCE", "readiness receipt is missing")
    expected_revision = f"main@sha1:{repository_revision}"
    _expect(
        readiness.get("revision") == expected_revision,
        "RUNTIME_ACCEPTANCE",
        "receipt revision does not bind repository and Flux",
    )
    kustomizations = readiness.get("kustomizations")
    _expect(
        isinstance(kustomizations, list)
        and [item.get("name") for item in kustomizations if isinstance(item, dict)]
        == EXPECTED_FLUX_ORDER
        and all(
            isinstance(item, dict)
            and item.get("ready") is True
            and item.get("revision") == expected_revision
            and item.get("reason") == "ReconciliationSucceeded"
            for item in kustomizations
        ),
        "RUNTIME_ACCEPTANCE",
        "Flux Ready evidence is invalid",
    )
    workloads = readiness.get("workloads")
    _expect(
        isinstance(workloads, dict)
        and workloads.get("polaris_deployment") == "Ready"
        and workloads.get("postgresql_statefulset") == "Ready"
        and workloads.get("postgresql_pvc") == "Bound"
        and isinstance(workloads.get("bootstrap_job"), str)
        and workloads["bootstrap_job"].startswith("polaris-bootstrap"),
        "RUNTIME_ACCEPTANCE",
        "workload Ready evidence is invalid",
    )
    secret_records = readiness.get("secrets")
    _expect(
        isinstance(secret_records, list)
        and {item.get("name") for item in secret_records if isinstance(item, dict)}
        == set(EXPECTED_SECRET_DATA_KEYS)
        and all(
            isinstance(item, dict)
            and item.get("managed_by") == "OpenTofu"
            and isinstance(item.get("generation"), str)
            and item["generation"].isdigit()
            and item.get("keys") == EXPECTED_SECRET_DATA_KEYS[item["name"]]
            for item in secret_records
        ),
        "RUNTIME_ACCEPTANCE",
        "OpenTofu Secret evidence is invalid",
    )
    smoke = receipt.get("catalog_api_smoke")
    _expect(
        isinstance(smoke, dict)
        and smoke.get("token_status") == 200
        and smoke.get("create_status") == 201
        and smoke.get("list_status") == 200
        and smoke.get("read_status") == 200
        and smoke.get("delete_status") == 204
        and smoke.get("cleanup_absent") is True
        and smoke.get("credential_material_retained") is False
        and smoke.get("storage_type") == "S3"
        and isinstance(smoke.get("base_location"), str)
        and smoke["base_location"].startswith("s3://shirokuma-lakehouse/"),
        "RUNTIME_ACCEPTANCE",
        "catalog API smoke evidence is invalid",
    )
    recovery = receipt.get("backup_restore")
    _expect(isinstance(recovery, dict), "RUNTIME_ACCEPTANCE", "backup receipt is missing")
    source = recovery.get("source_fingerprint")
    restored = recovery.get("restored_fingerprint")
    _expect(
        isinstance(recovery.get("backup_file"), str)
        and re.fullmatch(r"polaris-postgresql-[0-9]{8}T[0-9]{6}Z\.dump", recovery["backup_file"])
        is not None
        and isinstance(recovery.get("backup_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", recovery["backup_sha256"])
        is not None
        and isinstance(recovery.get("backup_bytes"), int)
        and recovery["backup_bytes"] > 0
        and isinstance(recovery.get("archive_entries"), int)
        and recovery["archive_entries"] > 0
        and recovery.get("backup_mode") == "0600"
        and recovery.get("host_root_mode") == "0700"
        and isinstance(recovery.get("host_free_kib"), int)
        and recovery["host_free_kib"] > 0
        and recovery.get("backup_location_policy")
        == "durable macOS host outside Colima"
        and recovery.get("postgresql_tools") == "pg_dump (PostgreSQL) 18.4"
        and recovery.get("fingerprints_match") is True
        and recovery.get("temporary_database_removed") is True
        and isinstance(source, dict)
        and source == restored
        and isinstance(source.get("table_count"), int)
        and source["table_count"] > 0
        and isinstance(source.get("row_count"), int)
        and source["row_count"] >= 0
        and re.fullmatch(r"[0-9a-f]{32}", str(source.get("schema_md5"))) is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(source.get("content_sha256"))) is not None,
        "RUNTIME_ACCEPTANCE",
        "backup/restore evidence is invalid",
    )
    rollback = receipt.get("rollback_teardown")
    _expect(
        isinstance(rollback, dict)
        and rollback.get("destructive_teardown_executed") is False
        and set(rollback.get("runbooks", []))
        == {
            "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md",
            "docs/design/08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo.md",
        },
        "RUNTIME_ACCEPTANCE",
        "rollback/teardown evidence is invalid",
    )
    _expect(
        receipt.get("secrets")
        == {
            "material_in_git": False,
            "material_in_receipt": False,
            "provisioner": "OpenTofu",
        },
        "RUNTIME_ACCEPTANCE",
        "receipt secret boundary changed",
    )


def _audit_runtime_inventory(root: Path, manifests: Mapping[str, Any]) -> None:
    found: set[str] = set()
    for relative_root in (Path("deploy/catalog"), Path("deploy/gitops/catalog")):
        runtime_root = root / relative_root
        if runtime_root.exists():
            found.update(
                path.relative_to(root).as_posix()
                for path in runtime_root.rglob("*")
                if path.is_file() or path.is_symlink()
            )
    for pattern in (
        "deploy/gitops/clusters/local-lite/catalog*.yaml",
        "opentofu/dev/catalog*.tf",
    ):
        found.update(
            path.relative_to(root).as_posix()
            for path in root.glob(pattern)
            if path.is_file() or path.is_symlink()
        )
    for relative in (
        "deploy/gitops/clusters/local-lite/kustomization.yaml",
        "deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml",
        "deploy/gitops/object-storage/statefulset.yaml",
    ):
        path = root / relative
        if path.is_file() or path.is_symlink():
            found.add(relative)
    _expect(
        found == set(manifests),
        "RUNTIME_MANIFEST",
        "catalog runtime inventory changed; "
        f"expected {sorted(manifests)}, found {sorted(found)}",
    )


def _audit_semantics(
    root: Path,
    texts: Mapping[str, str],
    documentation: Mapping[str, str],
) -> None:
    combined = "\n".join(texts.values())
    runbook = documentation[
        "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md"
    ]
    rotation_runbook = documentation[
        "docs/design/08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo.md"
    ]
    _expect("kind: Secret" not in combined, "RUNTIME_SECRET", "Secret manifests are forbidden")
    _expect("secretGenerator:" not in combined and "stringData:" not in combined, "RUNTIME_SECRET", "generated or inline Secret material is forbidden")
    for image in (POLARIS_IMAGE, POSTGRES_IMAGE, ADMIN_IMAGE):
        _expect(combined.count(image) == 1, "RUNTIME_IMAGES", f"image must occur exactly once: {image}")
    for secret, keys in EXPECTED_SECRET_REFS.items():
        _expect(secret in combined, "RUNTIME_SECRET", f"missing Secret reference: {secret}")
        for key in keys:
            _expect(key in combined, "RUNTIME_SECRET", f"missing Secret key reference: {secret}/{key}")

    server = texts["deploy/gitops/catalog/server/deployment.yaml"]
    _, env_marker, env_and_ports = server.partition("          env:\n")
    env_section, ports_marker, _ = env_and_ports.partition("          ports:\n")
    _expect(
        bool(env_marker and ports_marker),
        "RUNTIME_SECRET",
        "Polaris container environment boundary changed",
    )
    for variable, key in EXPECTED_POLARIS_STORAGE_ENV.items():
        block = (
            f"            - name: {variable}\n"
            "              valueFrom:\n"
            "                secretKeyRef:\n"
            "                  name: seaweedfs-s3-application-credentials\n"
            f"                  key: {key}\n"
        )
        _expect(
            server.count(block) == 1,
            "RUNTIME_SECRET",
            f"Polaris storage Secret binding changed: {variable}",
        )
        quoted_variable = rf'(?:{re.escape(variable)}|"{re.escape(variable)}"|\'{re.escape(variable)}\')'
        block_names = re.findall(
            rf"^\s*(?:-\s*)?name\s*:\s*{quoted_variable}\s*$",
            env_section,
            re.MULTILINE,
        )
        flow_names = re.findall(
            rf"(?:\{{|,)\s*name\s*:\s*{quoted_variable}(?=\s*[,}}])",
            env_section,
        )
        _expect(
            len(block_names) + len(flow_names) == 1,
            "RUNTIME_SECRET",
            f"Polaris storage environment name must occur exactly once: {variable}",
        )
    _expect(
        "AWS_SESSION_TOKEN" not in server,
        "RUNTIME_SECRET",
        "Polaris must not require an unprovisioned S3 session token",
    )
    _, metadata_marker, pod_metadata_and_spec = server.partition("    metadata:\n")
    pod_metadata, spec_marker, _ = pod_metadata_and_spec.partition("    spec:\n")
    _expect(
        bool(metadata_marker and spec_marker),
        "RUNTIME_NETWORK",
        "Polaris Pod template metadata boundary changed",
    )
    _expect(
        pod_metadata.count('shirokuma.dev/object-storage-client: "true"') == 1,
        "RUNTIME_NETWORK",
        "Polaris Pod must opt in to the SeaweedFS S3 ingress policy",
    )

    storage = texts["deploy/gitops/object-storage/statefulset.yaml"]
    generation_pattern = re.compile(
        r'^\s*shirokuma\.dev/s3-credential-generation: "([1-9][0-9]*)"$',
        re.MULTILINE,
    )
    server_generations = generation_pattern.findall(server)
    storage_generations = generation_pattern.findall(storage)
    _expect(
        len(server_generations) == 1
        and len(storage_generations) == 1
        and server_generations == storage_generations,
        "RUNTIME_GENERATION",
        "Polaris and SeaweedFS must consume the same S3 credential generation",
    )
    for token in (
        "deploy/gitops/catalog/server/deployment.yaml",
        "kubectl -n shirokuma-dev rollout status deployment/polaris",
        "and Polaris rollouts",
    ):
        _expect(
            token in rotation_runbook,
            "RUNTIME_GENERATION",
            f"S3 credential rotation runbook missing {token}",
        )

    job = texts["deploy/catalog/bootstrap/job.yaml"]
    _expect("--credentials-file=/var/run/secrets/shirokuma/polaris/credentials.json" in job, "RUNTIME_ADMIN", "credential-file input is missing")
    for forbidden in ("--credential=", "--realm=", "--print-credentials"):
        _expect(forbidden not in job, "RUNTIME_ADMIN", f"forbidden Admin argument: {forbidden}")
    _expect("readOnly: true" in job and "defaultMode: 0440" in job, "RUNTIME_ADMIN", "credential mount must be read-only 0440")
    _expect("activeDeadlineSeconds: 600" in job, "RUNTIME_ADMIN", "bootstrap Job must have a bounded execution deadline")
    for relative in (
        "deploy/catalog/bootstrap/job.yaml",
        "deploy/gitops/catalog/server/deployment.yaml",
    ):
        _expect(
            "POLARIS_REALM_CONTEXT_REALMS" in texts[relative]
            and "value: POLARIS" in texts[relative],
            "RUNTIME_REALM",
            f"{relative} must explicitly bind the POLARIS realm",
        )

    chain = {
        "deploy/gitops/clusters/local-lite/catalog-database.yaml": ("shirokuma-object-storage", "StatefulSet", "polaris-postgresql"),
        "deploy/gitops/clusters/local-lite/catalog-bootstrap.yaml": ("shirokuma-catalog-database", "Job", "polaris-bootstrap"),
        "deploy/gitops/clusters/local-lite/catalog.yaml": ("shirokuma-catalog-bootstrap", "Deployment", "polaris"),
    }
    for relative, (dependency, kind, name) in chain.items():
        text = texts[relative]
        for token in (
            f"- name: {dependency}",
            "prune: true",
            "wait: true",
            "timeout: 10m",
            "healthChecks:",
            f"kind: {kind}",
            f"name: {name}",
            "substitute:",
            "POLARIS_CREDENTIAL_GENERATION: __REPLACED_BY_ROOT_KUSTOMIZATION__",
        ):
            _expect(token in text, "RUNTIME_FLUX", f"{relative} missing {token}")
    _expect(
        "force: true"
        in texts["deploy/gitops/clusters/local-lite/catalog-bootstrap.yaml"],
        "RUNTIME_GENERATION",
        "bootstrap Flux Kustomization must recreate the immutable Job on generation change",
    )

    root_kustomization = texts[
        "deploy/gitops/clusters/local-lite/kustomization.yaml"
    ]
    for resource in (
        "flux-system",
        "dev.yaml",
        "object-storage.yaml",
        "polaris-runtime-generation.yaml",
        "catalog-database.yaml",
        "catalog-bootstrap.yaml",
        "catalog.yaml",
    ):
        _expect(
            f"- {resource}" in root_kustomization,
            "RUNTIME_FLUX",
            f"Flux root missing resource: {resource}",
        )
    for token in (
        "replacements:",
        "name: polaris-runtime-generation",
        "fieldPath: data.POLARIS_CREDENTIAL_GENERATION",
        "spec.postBuild.substitute.POLARIS_CREDENTIAL_GENERATION",
        "name: shirokuma-catalog-database",
        "name: shirokuma-catalog-bootstrap",
        "name: shirokuma-catalog",
    ):
        _expect(
            token in root_kustomization,
            "RUNTIME_GENERATION",
            f"Flux root credential-generation replacement missing {token}",
        )
    _expect(
        root_kustomization.count(
            "spec.postBuild.substitute.POLARIS_CREDENTIAL_GENERATION"
        )
        == 3,
        "RUNTIME_GENERATION",
        "Flux root must replace the generation value in exactly three catalog Kustomizations",
    )

    generation = texts[
        "deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml"
    ]
    _expect(
        "name: polaris-runtime-generation" in generation
        and 'POLARIS_CREDENTIAL_GENERATION: "1"' in generation,
        "RUNTIME_GENERATION",
        "credential generation source must be the reviewed positive token",
    )
    for relative in (
        "deploy/catalog/bootstrap/job.yaml",
        "deploy/gitops/catalog/database/statefulset.yaml",
        "deploy/gitops/catalog/server/deployment.yaml",
    ):
        _expect(
            texts[relative].count("${POLARIS_CREDENTIAL_GENERATION}") == 1
            and (
                'shirokuma.dev/polaris-credential-generation: '
                '"generation-${POLARIS_CREDENTIAL_GENERATION}"'
            )
            in texts[relative],
            "RUNTIME_GENERATION",
            f"{relative} must consume the shared generation as a type-stable string",
        )

    tofu = texts["opentofu/dev/catalog.tf"]
    for token in (
        'resource "kubernetes_secret_v1" "polaris_postgresql_credentials"',
        'resource "kubernetes_secret_v1" "polaris_root_credentials"',
        "var.polaris_postgresql_password",
        "var.polaris_root_client_secret",
        '"credentials.json" = local.polaris_root_credentials',
        "yamldecode(file(",
        "polaris-runtime-generation.yaml",
        "local.polaris_credential_generation",
    ):
        _expect(token in tofu, "RUNTIME_SECRET", f"OpenTofu credential boundary missing {token}")
    _expect(
        tofu.count("ignore_changes = [data]") == 2,
        "RUNTIME_GENERATION",
        "in-place Secret data rotation must remain blocked for both credentials",
    )
    _expect(
        "var.polaris_credential_generation" not in tofu,
        "RUNTIME_GENERATION",
        "credential generation may not be overridden independently through TF_VAR",
    )

    makefile = (root / "Makefile").read_text(encoding="utf-8")
    bootstrap = makefile.split("gitops-bootstrap:", 1)[1].split(
        "\ngitops-status:", 1
    )[0]
    teardown = makefile.split("gitops-teardown:", 1)[1].split(
        "\ncolima-start:", 1
    )[0]
    for variable in ("TF_VAR_polaris_postgresql_password", "TF_VAR_polaris_root_client_secret"):
        for target, recipe in (("bootstrap", bootstrap), ("teardown", teardown)):
            _expect(
                f"$${{{variable}:-}}" in recipe,
                "RUNTIME_SECRET",
                f"{target} preflight missing {variable}",
            )

    cleanup_start = "After bootstrap completes"
    cleanup_end = "Confirm controller readiness"
    _expect(
        cleanup_start in runbook and cleanup_end in runbook,
        "RUNTIME_SECRET",
        "bootstrap cleanup section is missing",
    )
    bootstrap_cleanup = runbook.split(cleanup_start, 1)[1].split(
        cleanup_end, 1
    )[0]
    for variable in (
        "TF_VAR_polaris_postgresql_password",
        "TF_VAR_polaris_root_client_secret",
    ):
        _expect(
            f"unset {variable}" in bootstrap_cleanup,
            "RUNTIME_SECRET",
            f"bootstrap cleanup missing {variable}",
        )

    for token in (
        "data-polaris-postgresql-0",
        "reserve `25Gi`",
        "pg_dump",
        "pg_restore",
        "polaris.dump.sha256",
        "destructive path remains a recovery runbook",
        "capture-polaris-runtime-acceptance",
        "isolated temporary database",
        "all six required S3 and Polaris variables",
    ):
        _expect(
            token in runbook,
            "RUNTIME_RECOVERY",
            f"PostgreSQL capacity/recovery runbook missing {token}",
        )


def audit(root: Path) -> None:
    root = root.resolve()
    contract = _audit_contract(root)
    texts = _audit_manifests(root, contract)
    documentation = _audit_documentation(root, contract)
    _audit_tooling(root, contract)
    _audit_live_acceptance(root, contract)
    _audit_runtime_inventory(root, _manifest_map(contract))
    _audit_semantics(root, texts, documentation)


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
    except RuntimeContractError as error:
        print(f"{error.code}: {error.detail}", file=sys.stderr)
        return 1
    print("Polaris runtime activation contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
