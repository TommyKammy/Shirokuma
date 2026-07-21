#!/usr/bin/env python3
"""Fail-closed static audit for the credential-safe Polaris runtime activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
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


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail("RUNTIME_CONTRACT", f"cannot read {path}: {error}")
    _expect(isinstance(value, dict), "RUNTIME_CONTRACT", "contract must be an object")
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
        "manifests",
        "live_acceptance",
    }
    _expect(set(contract) == expected_keys, "RUNTIME_CONTRACT", "contract key set changed")
    _expect(contract.get("schema_version") == 1, "RUNTIME_CONTRACT", "schema_version must be 1")
    _expect(contract.get("issue") == 61, "RUNTIME_CONTRACT", "issue must be 61")
    _expect(
        contract.get("state") == "runtime_acceptance_pending",
        "RUNTIME_CONTRACT",
        "state must remain runtime_acceptance_pending",
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
        contract.get("live_acceptance")
        == {
            "complete": False,
            "required": [
                "flux_ready",
                "catalog_create_list_read",
                "backup_restore",
                "rollback_teardown",
            ],
        },
        "RUNTIME_ACCEPTANCE",
        "live acceptance must remain explicitly pending",
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


def _audit_documentation(root: Path, contract: Mapping[str, Any]) -> str:
    documentation = _documentation_map(contract)
    relative = "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md"
    _expect(
        set(documentation) == {relative},
        "RUNTIME_DOCUMENTATION",
        "runtime recovery documentation set changed",
    )
    expected_digest = documentation[relative]
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
    return path.read_text(encoding="utf-8")


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


def _audit_semantics(root: Path, texts: Mapping[str, str], runbook: str) -> None:
    combined = "\n".join(texts.values())
    _expect("kind: Secret" not in combined, "RUNTIME_SECRET", "Secret manifests are forbidden")
    _expect("secretGenerator:" not in combined and "stringData:" not in combined, "RUNTIME_SECRET", "generated or inline Secret material is forbidden")
    for image in (POLARIS_IMAGE, POSTGRES_IMAGE, ADMIN_IMAGE):
        _expect(combined.count(image) == 1, "RUNTIME_IMAGES", f"image must occur exactly once: {image}")
    for secret, keys in EXPECTED_SECRET_REFS.items():
        _expect(secret in combined, "RUNTIME_SECRET", f"missing Secret reference: {secret}")
        for key in keys:
            _expect(key in combined, "RUNTIME_SECRET", f"missing Secret key reference: {secret}/{key}")

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
            texts[relative].count("${POLARIS_CREDENTIAL_GENERATION}") == 1,
            "RUNTIME_GENERATION",
            f"{relative} must consume the shared credential generation exactly once",
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

    for token in (
        "data-polaris-postgresql-0",
        "reserve `25Gi`",
        "pg_dump",
        "pg_restore",
        "polaris.dump.sha256",
        "whole-profile metadata restore remains blocked",
        "all six required S3 and Polaris variables",
        "unset TF_VAR_polaris_postgresql_password",
        "unset TF_VAR_polaris_root_client_secret",
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
    runbook = _audit_documentation(root, contract)
    _audit_runtime_inventory(root, _manifest_map(contract))
    _audit_semantics(root, texts, runbook)


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
