#!/usr/bin/env python3
"""Fail-closed verifier for the temporary Trino 483 dependency publisher."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


CONTRACT_PATH = Path("bootstrap/trino/v483/trusted-build-contract.json")
ADMISSION_PATH = Path("bootstrap/trino/v483/admission.json")
SETTINGS_PATH = Path("bootstrap/trino/v483/settings.xml")
JVM_CONFIG_PATH = Path("bootstrap/trino/v483/maven-policy/.mvn/jvm.config")
WORKFLOW_PATH = Path(".github/workflows/trino-maven-dependencies.yml")
PACKAGER_PATH = Path("scripts/package_trino_maven_dependencies.py")
BUN_PACKAGER_PATH = Path("scripts/package_trino_bun_dependencies.py")
BUN_PREPARER_PATH = Path("scripts/prepare_trino_bun_input.py")
PARQUET_REMEDIATION_PATH = Path("scripts/remediate_parquet_jackson.py")
VERIFIER_PATH = Path("scripts/verify_trino_dependency_publisher.py")
FEASIBILITY_VERIFIER_PATH = Path("scripts/verify_trino_maven_feasibility.py")
TEST_PATH = Path("tests/test_trino_dependency_publisher.py")
FEASIBILITY_TEST_PATH = Path("tests/test_trino_maven_feasibility.py")
BUN_TEST_PATH = Path("tests/test_trino_bun_dependencies.py")
PARQUET_REMEDIATION_TEST_PATH = Path(
    "tests/test_parquet_jackson_remediation.py"
)
GITHUB_API_RESPONSE_BYTES = 1_048_576
GITHUB_COMMENT_PAGE_RESPONSE_BYTES = 33_554_432
GITHUB_PULL_REVIEW_PAGE_SIZE = 100
GITHUB_PULL_REVIEW_MAXIMUM_PAGES = 10
GITHUB_PULL_REVIEW_MAXIMUM_REVIEWS = 999
GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES = 33_554_432
GITHUB_OWNER_COMMENT_MAXIMUM_ITEMS = 999
GITHUB_PULL_BINDING_PAGE_SIZE = 100
GITHUB_PULL_BINDING_MAXIMUM_PAGES = 10
GITHUB_PULL_BINDING_MAXIMUM_PULL_REQUESTS = 999
GITHUB_PULL_BINDING_PAGE_RESPONSE_BYTES = 4_194_304
GITHUB_WORKFLOW_RUN_PAGE_SIZE = 100
GITHUB_WORKFLOW_RUN_MAXIMUM_PAGES = 10
GITHUB_WORKFLOW_RUN_MAXIMUM_RUNS = 999
GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES = 4_194_304
GITHUB_REVIEW_THREAD_PAGE_SIZE = 100
GITHUB_REVIEW_THREAD_MAXIMUM_PAGES = 10
GITHUB_REVIEW_THREAD_MAXIMUM_THREADS = 1_000
MAX_OMITTED_JAR_MEMBER_BYTES = 64 * 1024 * 1024
MAX_OMITTED_JAR_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_OMITTED_JAR_COMPRESSION_RATIO = 200
MAX_OMITTED_JAR_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_OMITTED_JAR_MEMBERS = 100_000
MAX_OMITTED_JAR_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_BUILDER_SETTINGS_BYTES = 1024 * 1024
EXPECTED_AUTHORIZATION = {
    "type": "time_boxed_source_identity_risk_acceptance",
    "decision_record": (
        "docs/design/07_ADR/"
        "ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC.md"
    ),
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/"
        "issues/63#issuecomment-5052385803"
    ),
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "approved_at": "2026-07-22T22:43:36Z",
    "expires_at": "2026-08-21T22:43:36Z",
    "maximum_duration_days": 30,
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "implementation_author": "Codex",
    "review": {
        "required_before_merge": True,
        "reviewer_must_differ_from_implementation_author": True,
        "enforcement": "required_pull_request_review_before_merge",
    },
    "validation_points": [
        "before_source_fetch",
        "before_source_execution",
        "before_dependency_resolution",
        "before_dependency_publication",
        "before_evidence_review",
    ],
    "scope": {
        "profile": "mac-studio-solo/local-lite",
        "purpose": "non-production-poc",
        "data_classification": ["synthetic", "poc"],
        "public_service_or_ingress_permitted": False,
        "source_binding": {
            "repository": "https://github.com/trinodb/trino",
            "release_tag": "483",
            "commit_sha": "50b0b50b75abd47f830b7805ee1b51716eb4065e",
            "tree_sha": "3b5414292a614b12393bb4605ea2d4c588a5b8ee",
        },
    },
    "accepted_risk": (
        "the exact source binding lacks a qualifying upstream publisher "
        "signature or provenance statement"
    ),
    "stacked_vulnerability_exception_permitted": False,
    "expiry_action": (
        "fail_closed_before_dependency_or_image_publication_"
        "resident_admission_or_runtime_reconciliation"
    ),
}
EXPECTED_PUBLICATION_ATTEMPT = {
    "event_name": "push",
    "ref": "refs/heads/main",
    "before_sha": "e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823",
    "run_attempt": "1",
}
EXPECTED_INDEPENDENT_REVIEW = {
    "required_before_merge": True,
    "required_before_publication": True,
    "minimum_approved_reviews": 1,
    "human_user_type": "User",
    "reviewer_must_differ_from_risk_owner": True,
    "reviewer_must_differ_from_implementation_author": True,
    "approval_must_match_final_head_sha": True,
    "approval_must_be_submitted_before_merge": True,
    "reviews": {
        "api": "rest_pull_request_reviews",
        "page_size": GITHUB_PULL_REVIEW_PAGE_SIZE,
        "maximum_pages": GITHUB_PULL_REVIEW_MAXIMUM_PAGES,
        "maximum_reviews": GITHUB_PULL_REVIEW_MAXIMUM_REVIEWS,
        "maximum_page_bytes": GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES,
        "stable_snapshot_passes": 2,
        "unique_review_ids": True,
        "pagination_must_be_complete": True,
    },
    "publication_enforcement": (
        "exact_merged_pull_request_review_or_owner_attestation_query"
    ),
}
EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION = {
    "status": "consumed_failed_closed",
    "scope": "pr_148_fifth_publication_attempt_only",
    "reason": "sole_owner_personal_experimental_project",
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5268936554"
    ),
    "approved_at": "2026-08-12T15:32:31Z",
    "repository": "TommyKammy/Shirokuma",
    "pull_request": 148,
    "owner": "TommyKammy",
    "human_user_type": "User",
    "author_association": "OWNER",
    "attestation_required_before_merge": True,
    "attestation_must_match_final_head_sha": True,
    "attestation_body_template": (
        "Owner final-head attestation for PR #148\n\n"
        "Decision: {decision}\n"
        "Final head: {final_head}\n"
        "Exception: https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5268936554"
    ),
    "allowed_decisions": ["APPROVED", "REVOKED"],
    "consumed_run": {
        "run_id": "31616764771",
        "run_attempt": "1",
        "source_sha": "49a86522d6e6c69f4a552220b30fa510d3a5edd2",
        "result": "failed_closed_before_registry_authentication",
        "rerun_permitted": False,
    },
    "final_head_ci": {
        "required_before_attestation": True,
        "workflow_paths": [
            ".github/workflows/ci.yml",
            ".github/workflows/security.yml",
            ".github/workflows/trino-maven-remediation-feasibility.yml",
            ".github/workflows/trino-maven-dependencies.yml",
        ],
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha_must_match_attestation": True,
        "pull_request_must_match": True,
        "api": "rest_workflow_runs",
        "page_size": GITHUB_WORKFLOW_RUN_PAGE_SIZE,
        "maximum_pages": GITHUB_WORKFLOW_RUN_MAXIMUM_PAGES,
        "maximum_runs": GITHUB_WORKFLOW_RUN_MAXIMUM_RUNS,
        "maximum_page_bytes": GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES,
        "stable_snapshot_passes": 2,
        "same_path_run_selection": (
            "latest_qualifying_success_attestation_preceding_by_updated_created_id"
        ),
        "positive_unique_run_ids": True,
        "total_count_must_match": True,
        "pagination_must_be_complete": True,
    },
    "review_threads": {
        "required_before_attestation": True,
        "current_non_outdated": 0,
        "head_sha_must_match_attestation": True,
        "query": "graphql_review_threads",
        "page_size": GITHUB_REVIEW_THREAD_PAGE_SIZE,
        "maximum_pages": GITHUB_REVIEW_THREAD_MAXIMUM_PAGES,
        "maximum_threads": GITHUB_REVIEW_THREAD_MAXIMUM_THREADS,
        "stable_snapshot_passes": 2,
        "unique_thread_ids": True,
        "total_count_must_match": True,
        "pagination_must_be_complete": True,
    },
    "owner_issue_comments": {
        "query_only_if_standard_independent_review_absent": True,
        "api": "rest_issue_comments",
        "page_size": 100,
        "maximum_pages": 10,
        "maximum_items": GITHUB_OWNER_COMMENT_MAXIMUM_ITEMS,
        "maximum_page_bytes": GITHUB_COMMENT_PAGE_RESPONSE_BYTES,
        "stable_snapshot_passes": 2,
        "revalidate_after_final_api_gates": True,
        "revalidated_decision_must_match": True,
        "strictly_increasing_unique_ids": True,
        "pagination_must_be_complete": True,
    },
    "publication_enforcement": (
        "exact_merged_pull_request_attested_head_ci_and_review_threads_query"
    ),
    "failure_consumes_attempt": True,
    "rerun_permitted": False,
    "downstream_authorities_granted": [],
    "standard_independent_review_remains_accepted": True,
}
EXPECTED_PENDING_REVIEW_REPAIR = {
    "status": "review_pending_not_authorized",
    "scope": "final_head_pull_request_association_only",
    "triggering_run_id": "31616764771",
    "triggering_run_attempt": "1",
    "failed_step": "Revalidate the write-capable publication boundary",
    "observed_workflow_run_pull_requests": [],
    "root_cause": (
        "workflow_run_pull_requests_is_not_a_durable_post_merge_binding"
    ),
    "pull_request_binding": {
        "apis": [
            "rest_merge_commit_associated_pulls",
            "rest_final_head_associated_pulls",
            "rest_head_filtered_pulls",
        ],
        "page_size": GITHUB_PULL_BINDING_PAGE_SIZE,
        "maximum_pages": GITHUB_PULL_BINDING_MAXIMUM_PAGES,
        "maximum_pull_requests": GITHUB_PULL_BINDING_MAXIMUM_PULL_REQUESTS,
        "maximum_page_bytes": GITHUB_PULL_BINDING_PAGE_RESPONSE_BYTES,
        "stable_snapshot_passes": 2,
        "pagination_must_be_complete": True,
        "exact_singleton_required": True,
        "workflow_query_branch_must_match_head_ref": True,
        "workflow_run_pull_requests_policy": "empty_or_exact_single_target",
    },
    "publication_permissions_enabled": False,
    "applies_only_after_separate_owner_authorization": True,
    "next_sequence_authorized": False,
}
EXPECTED_PUBLICATION_REAUTHORIZATION = {
    "sequence": 5,
    "status": "consumed_failed_closed",
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5268936554"
    ),
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "approved_at": "2026-08-12T15:32:31Z",
    "expires_at": "2026-08-21T22:43:36Z",
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "same_candidate_required": True,
    "publication_authorized_after_required_approval": False,
    "next_sequence_authorized": False,
    "previous_attempt": {
        "run_id": "31605249586",
        "run_attempt": "1",
        "source_sha": "e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823",
        "result": "failed_closed_before_publication",
        "reason": "react_router_fixed_version_metadata_drift",
        "raw_high_findings": 1,
        "adjusted_high_findings": 0,
        "adjusted_critical_findings": 0,
        "retained_artifact_count": 1,
        "retained_diagnostic_artifact": (
            "trino-bun-vulnerability-diagnostics-31605249586-1"
        ),
        "dependency_artifact_published": False,
        "consumed": True,
    },
    "repair": {
        "pull_request": "https://github.com/TommyKammy/Shirokuma/pull/148",
        "predecessor_main_commit": (
            "e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823"
        ),
        "web_ui_overlay_paths": [
            "core/trino-web-ui/src/main/resources/webapp/package.json",
            "core/trino-web-ui/src/main/resources/webapp/bun.lock",
            "core/trino-web-ui/src/main/resources/webapp-legacy/src/package.json",
            "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock",
        ],
        "react_router_openvex_only": True,
        "raw_finding_fixed_version": "7.18.2, 8.3.0",
        "other_raw_finding_identity_fields_unchanged": True,
        "react_router_upgrade_admitted": False,
        "adjusted_scan_report_exit_code": 0,
        "explicit_scan_verifier_required": True,
        "diagnostic_artifact_on_scan_gate_failure": True,
        "diagnostic_artifact_publication_input_permitted": False,
        "offline_repository_root": (
            "${RUNNER_TEMP}/trino-offline-maven-repository-${suffix}"
        ),
        "container_mount": "/m2:ro",
        "maven_repo_local": "/m2",
        "maven_lock_directory": "/tmp/maven-locks",
        "maven_goal": "clean package",
        "untracked_source_rejection_retained": True,
    },
    "outcome": {
        "pull_request": 148,
        "pull_request_final_head": (
            "7581b2413c1c820ac1f774fe28034f1b7bfa6eb1"
        ),
        "merge_commit": "49a86522d6e6c69f4a552220b30fa510d3a5edd2",
        "owner_attestation_comment_id": 5269416490,
        "owner_attested_at": "2026-08-12T16:15:56Z",
        "run_id": "31616764771",
        "run_attempt": "1",
        "event_name": "push",
        "ref": "refs/heads/main",
        "before_sha": "e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823",
        "source_sha": "49a86522d6e6c69f4a552220b30fa510d3a5edd2",
        "validate_job": "success",
        "publish_job": "failure",
        "failed_step": "Revalidate the write-capable publication boundary",
        "failure_code": "INDEPENDENT_REVIEW",
        "failure_reason": "workflow_run_pull_request_association_missing",
        "final_head_ci_run_ids": {
            ".github/workflows/ci.yml": 31615663629,
            ".github/workflows/security.yml": 31615663622,
            ".github/workflows/trino-maven-remediation-feasibility.yml": (
                31615663614
            ),
            ".github/workflows/trino-maven-dependencies.yml": 31615663628,
        },
        "workflow_run_pull_requests_observed": [],
        "candidate_artifact": {
            "id": 9150299769,
            "name": "trino-maven-candidate-31616764771-1",
            "bytes": 844111993,
            "expires_at": "2026-08-13T16:39:07Z",
            "expired": True,
        },
        "candidate_download_reached": False,
        "registry_authentication_reached": False,
        "registry_write_reached": False,
        "dependency_artifact_published": False,
        "final_publication_artifact_present": False,
        "consumed": True,
    },
    "failure_consumes_attempt": True,
    "rerun_permitted": False,
}
SOURCE_OVERLAY_PATH = Path(
    "bootstrap/trino/v483/patches/0001-shirokuma-web-ui-security.patch"
)
DISTRIBUTION_REMEDIATION_PATH = Path(
    "bootstrap/trino/v483/patches/"
    "0002-shirokuma-iceberg-only-maven-closure.patch"
)
BUILD_PLUGIN_REMEDIATION_PATH = Path(
    "bootstrap/trino/v483/patches/"
    "0003-shirokuma-maven-build-plugin-closure.patch"
)
VEX_PATH = Path(
    "bootstrap/trino/v483/vex/"
    "react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json"
)
OVERLAY_ADR_PATH = Path(
    "docs/design/07_ADR/"
    "ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX.md"
)
PARQUET_REMEDIATION_ADR_PATH = Path(
    "docs/design/07_ADR/"
    "ADR-0026_Authorize_bounded_Parquet_Jackson_1_17_1_source_remediation.md"
)
DISTRIBUTION_REMEDIATION_ADR_PATH = Path(
    "docs/design/07_ADR/"
    "ADR-0027_Authorize_bounded_Trino_483_Iceberg_only_Maven_closure.md"
)
BLOCKER_ADR_PATH = Path(
    "docs/design/07_ADR/"
    "ADR-0028_Keep_Trino_483_publisher_blocked_for_refreshed_Maven_findings.md"
)
BUILD_PLUGIN_REMEDIATION_ADR_PATH = Path(
    "docs/design/07_ADR/"
    "ADR-0029_Authorize_exact_Trino_483_Maven_build_plugin_remediation.md"
)
AUTHORIZED_FEASIBILITY_RECORD_PATH = Path(
    "docs/design/evidence/trino/"
    "run-31072144404-maven-feasibility-validation.json"
)
AUTHORIZED_FEASIBILITY_RECEIPT_PATH = Path(
    "docs/design/evidence/trino/"
    "run-31072144404-maven-feasibility-artifact-receipt.json"
)
BLOCKER_CLASSIFICATION_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30693677356-maven-vulnerability-classification.json"
)
BLOCKER_BASELINE_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30693677356-post-adr-0027-pom.xml.gz"
)
BLOCKER_HARDENED_SCM_POM_PATH = Path(
    "bootstrap/trino/v483/"
    "maven-scm-provider-gitexe-2.2.1-hardened.pom"
)
BLOCKER_HARDENED_SCM_MANAGER_POM_PATH = Path(
    "bootstrap/trino/v483/"
    "maven-scm-manager-plexus-2.2.1-hardened.pom"
)
BLOCKER_FEASIBILITY_RECORD_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30731801825-maven-feasibility-validation.json"
)
BLOCKER_FEASIBILITY_RECEIPT_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30731801825-maven-feasibility-artifact-receipt.json"
)
SUPERSEDED_FEASIBILITY_RECORD_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30724152120-maven-feasibility-validation.json"
)
SUPERSEDED_FEASIBILITY_RECEIPT_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30724152120-maven-feasibility-artifact-receipt.json"
)
FEASIBILITY_VERIFIER_PATH = Path(
    "scripts/verify_trino_maven_feasibility.py"
)
FEASIBILITY_RETAINED_VERIFIER_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30731801825-verify-trino-maven-feasibility.py"
)
EXPECTED_FEASIBILITY_REAUDIT = {
    "artifact_digest": (
        "sha256:cf0272447ec1a6afd4bda304fefeb6176ee4240d4fc6339a32de65acf015fe8d"
    ),
    "audited_at": "2026-08-05T03:43:58Z",
    "result": "passed_integrity_only",
    "scope": (
        "complete retained artifact structural and content integrity; "
        "execution controls are not retroactively proven"
    ),
    "verifier": {
        "path": FEASIBILITY_VERIFIER_PATH.as_posix(),
        "sha256": (
            "3231b841f85e720ee75d3ffe6833ef4c852ee5937955b017f3a3b0b621e9fd10"
        ),
    },
}
EXPECTED_BLOCKER_FEASIBILITY_FILES = {
    SUPERSEDED_FEASIBILITY_RECORD_PATH: {
        "bytes": 4675,
        "sha256": (
            "a26e2a839003897583a82349e7d36a637b2d34856e776f2c91e2aa7e208147c7"
        ),
    },
    SUPERSEDED_FEASIBILITY_RECEIPT_PATH: {
        "bytes": 899,
        "sha256": (
            "181604f862e41c51041b3df950a175faf7a6f7bc74530ef0ae0bc9434e49e854"
        ),
    },
    BLOCKER_FEASIBILITY_RECORD_PATH: {
        "bytes": 5793,
        "sha256": (
            "7f87f42ee02960729cacd8dbaea5e6b73e7714b46293149b4bdb9bd8d19f6015"
        ),
    },
    BLOCKER_FEASIBILITY_RECEIPT_PATH: {
        "bytes": 1401,
        "sha256": (
            "d336848e80052ae194c68320bd646978758017361df18dd213989c462026a046"
        ),
    },
}
EXPECTED_BLOCKER_SUBJECT = {
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "workflow_run": (
        "https://github.com/TommyKammy/Shirokuma/actions/runs/30693677356"
    ),
    "run_id": 30693677356,
    "run_attempt": 1,
    "reviewed_main_commit": "7d3301ccdf06e42b3bf1300d813e45887193c2be",
    "workflow": "Trino 483 build dependency snapshot",
    "failed_job": "validate",
    "failed_step": "Verify and block the complete Maven JAR scan inventory",
    "failure_code": "MAVEN_SCAN_FINDING",
    "diagnostic_artifact": {
        "name": "trino-maven-vulnerability-diagnostics-30693677356-1",
        "artifact_id": 8816735183,
        "zip_sha256": (
            "6478db69e063c0bde104a023ac4bbd430ce607bfd61f566327dc90f9ccfb3bbe"
        ),
        "expires_at": "2026-08-15T09:45:13Z",
    },
}
EXPECTED_BLOCKER_INPUTS = {
    "raw_trivy_report": {
        "path": (
            "docs/design/evidence/trino/"
            "run-30693677356-trivy-vulnerability.json"
        ),
        "sha256": (
            "e2d843dee383b619eabb915a5568b4478bb197f38a3097d32bf39bc5c7f462f2"
        ),
        "bytes": 526660,
    },
    "closure_complete_sbom": {
        "path": (
            "docs/design/evidence/trino/"
            "run-30693677356-maven-closure.cdx.json"
        ),
        "sha256": (
            "6ccb4a7ea44d8067094c378d7cf71893cacc091957b2a53b23fd1414c82b04ba"
        ),
        "bytes": 804639,
    },
    "run_scoped_maven_manifest": {
        "path": (
            "docs/design/evidence/trino/"
            "run-30693677356-maven-dependency-manifest.json"
        ),
        "sha256": (
            "1d1c88ffdcca88befae8b276611b83e70131b32be4520fbdb660094277323f69"
        ),
        "bytes": 1758953,
    },
    "raw_rootfs_sbom": {
        "path": (
            "docs/design/evidence/trino/"
            "run-30693677356-maven-rootfs.cdx.json"
        ),
        "sha256": (
            "405c2c8cd6cab23f5783c29ef3e818cc4e32de8aa52c65ebd5720592b150f55e"
        ),
        "bytes": 858534,
    },
}
EXPECTED_BLOCKER_SUMMARY = {
    "high_occurrences": 3,
    "critical_occurrences": 0,
    "vulnerability_ids": 2,
    "package_version_groups": 3,
    "physical_jar_paths": 3,
    "publication_reached": False,
    "dependency_artifact_produced": False,
    "admission_reached": False,
    "runtime_state_changed": False,
}
EXPECTED_BLOCKER_BASELINE = {
    "source_path": "pom.xml",
    "post_adr_0027_sha256": (
        "8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1"
    ),
    "retained_path": BLOCKER_BASELINE_PATH.as_posix(),
    "retained_sha256": (
        "dc5cfc5cd0ef38f2960926b364c32f476c1c94e949e0fa43711f17d951eb9b75"
    ),
    "retained_bytes": 13_531,
    "compression": "gzip",
}
EXPECTED_BLOCKER_CANDIDATE = {
    "source_path": "pom.xml",
    "postimage_sha256": (
        "871c6b21cf9fc70c455d21b64d24dd4501a8b5943242418edc2b2f5cfe14fab8"
    ),
    "patch_path": (
        "docs/design/evidence/trino/"
        "run-30693677356-proposed-source-overlay.patch"
    ),
    "patch_sha256": (
        "731e76f296a725d34ea9e226a1815782168cae3890424e69f76a05530afc15be"
    ),
    "patch_bytes": 8163,
    "application": "git apply --unidiff-zero --whitespace=error-all",
    "changed_paths": ["pom.xml"],
    "dependency_replacements": {
        "org.apache.velocity:velocity-engine-core": "2.4.1",
        "org.codehaus.plexus:plexus-utils": "4.0.3",
    },
    "extension_policy": {
        "io.github.gitflow-incremental-builder:gitflow-incremental-builder": (
            "removed_for_candidate"
        ),
    },
    "repository_metadata_remediation": [
        {
            "reviewed_source": (
                "bootstrap/trino/v483/"
                "maven-scm-provider-gitexe-2.2.1-hardened.pom"
            ),
            "target_path": (
                "org/apache/maven/scm/maven-scm-provider-gitexe/2.2.1/"
                "maven-scm-provider-gitexe-2.2.1.pom"
            ),
            "preimage_sha256": (
                "81521b7b72ca795c95ef5f377e410e7d2644d2ffbce03e34eeea73246847be08"
            ),
            "preimage_bytes": 2689,
            "postimage_sha256": (
                "0652487bb3cd532ce6ba9fd841c7f2346c1192b3271996a06ddd50f3052186a6"
            ),
            "postimage_bytes": 2720,
            "postimage_sha1": "a8630355e52d9c81dbd6ec117820bb58b6355f4a",
        },
        {
            "reviewed_source": (
                "bootstrap/trino/v483/"
                "maven-scm-manager-plexus-2.2.1-hardened.pom"
            ),
            "target_path": (
                "org/apache/maven/scm/maven-scm-manager-plexus/2.2.1/"
                "maven-scm-manager-plexus-2.2.1.pom"
            ),
            "preimage_sha256": (
                "7e1458bc8212c430c269c3d59063640b2164e6750f23539e6d6ca89d7207b3c5"
            ),
            "preimage_bytes": 1802,
            "postimage_sha256": (
                "4e7b25d9f3dfd21b874593edf794270888c8ef13bc29394b0da1c1cbefa41c43"
            ),
            "postimage_bytes": 1957,
            "postimage_sha1": "eb1b7ab169dc923806b0040631a45dc83d0b83e8",
        },
    ],
}
EXPECTED_BLOCKER_CLASSIFICATION = {
    "gate_status": "blocked",
    "vulnerability_waiver_permitted": False,
    "openvex_expansion_permitted": False,
    "scanner_suppression_permitted": False,
    "existing_distribution_authorization_covers_change": False,
    "reason": (
        "ADR-0027 fixes the active source-overlay patch SHA-256, pom.xml "
        "postimage, and allowed dependency versions. The retained feasibility "
        "artifact predates the current authorization checkpoints, read-only "
        "source mounts, and effective Maven policy basedir binding. Publication "
        "must remain blocked pending a fresh hardened validation run, separate "
        "exact owner authorization, and independent review."
    ),
}
EXPECTED_BLOCKER_FEASIBILITY_VALIDATION = {
    "evidence_status": "passed_integrity_reaudit_pre_hardening_execution",
    "authorization_use_permitted": False,
    "revalidation_required_before_authorization": True,
    "reproducible_inputs_retained": True,
    "validation_record": BLOCKER_FEASIBILITY_RECORD_PATH.as_posix(),
    "artifact_receipt": BLOCKER_FEASIBILITY_RECEIPT_PATH.as_posix(),
    "workflow_run": (
        "https://github.com/TommyKammy/Shirokuma/actions/runs/30731801825"
    ),
    "reviewed_commit": "3ceae605187b9e08f4f6e3a1d547f5623cfb111f",
    "artifact_id": 8828209533,
    "artifact_digest": (
        "sha256:cf0272447ec1a6afd4bda304fefeb6176ee4240d4fc6339a32de65acf015fe8d"
    ),
    "artifact_expires_at": "2026-09-01T04:08:14Z",
    "independent_reaudit": EXPECTED_FEASIBILITY_REAUDIT,
    "builder": (
        "docker.io/library/maven@sha256:"
        "7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c"
    ),
    "selected_reactor": (
        ":trino-server,:trino-server-core,:trino-server-main,"
        ":trino-hdfs,:trino-iceberg"
    ),
    "goal": "dependency:resolve-plugins -DskipTests",
    "reported_observations": {
        "online_resolution": "success",
        "network_none_offline_replay": True,
        "offline_replay": "success",
        "offline_repository_mount": "read-only",
        "archive_manifest_audit": "passed",
        "physical_vulnerable_jars": 0,
        "vulnerable_coordinate_lines": 0,
        "toolchain_inputs_retained": True,
    },
    "limitations": {
        "command_output_retained": True,
        "offline_repository_retained_in_actions_artifact": True,
        "artifact_retention_is_time_bounded": True,
        "hardened_integrity_reaudit_passed": True,
        "current_workflow_controls_proven": False,
        "read_only_source_mounts_proven": False,
        "effective_policy_basedir_proven": False,
        "retained_vulnerable_jar_detected": None,
        "full_clean_install_not_run": True,
        "fresh_closure_sbom_and_scan_not_run": True,
    },
}
EXPECTED_BLOCKER_NEXT_ACTION = {
    "state": "hardened_revalidation_required_before_owner_authorization",
    "required_decision": (
        "Generate a fresh artifact with the current hardened workflow, then "
        "review and approve or reject the exact candidate. Do not activate "
        "the source remediation or publish an image until hardened "
        "revalidation, separate risk-owner authorization, and independent "
        "review are complete."
    ),
}
EXPECTED_BLOCKER_FINDING_POLICY = [
    {
        "vulnerability_id": "CVE-2024-47554",
        "purl": "pkg:maven/commons-io/commons-io@2.8.0",
        "installed_version": "2.8.0",
        "physical_path": (
            "org/apache/velocity/velocity-engine-core/2.3/"
            "velocity-engine-core-2.3.jar"
        ),
        "classification": "embedded_shaded_component",
        "dependency_sources": ["org.revapi:revapi-maven-plugin:0.15.1"],
    },
    {
        "vulnerability_id": "CVE-2025-67030",
        "purl": "pkg:maven/org.codehaus.plexus/plexus-utils@4.0.1",
        "installed_version": "4.0.1",
        "physical_path": (
            "org/codehaus/plexus/plexus-utils/4.0.1/"
            "plexus-utils-4.0.1.jar"
        ),
        "classification": "top_level_maven_plugin_dependency",
        "dependency_sources": [
            "org.apache.maven.plugins:maven-checkstyle-plugin:3.6.0",
            "org.apache.maven.plugins:maven-deploy-plugin:3.1.4",
            "org.apache.maven.plugins:maven-install-plugin:3.1.4",
        ],
    },
    {
        "vulnerability_id": "CVE-2025-67030",
        "purl": "pkg:maven/org.codehaus.plexus/plexus-utils@4.0.2",
        "installed_version": "4.0.2",
        "physical_path": (
            "org/codehaus/plexus/plexus-utils/4.0.2/"
            "plexus-utils-4.0.2.jar"
        ),
        "classification": "top_level_maven_plugin_dependency",
        "dependency_sources": [
            "com.mycila:license-maven-plugin:5.0.0",
            "org.apache.maven.plugins:maven-assembly-plugin:3.8.0",
            "org.apache.maven.plugins:maven-clean-plugin:3.5.0",
            "org.apache.maven.plugins:maven-javadoc-plugin:3.12.0",
            "org.apache.maven.plugins:maven-pmd-plugin:3.28.0",
            "org.apache.maven.plugins:maven-release-plugin:3.3.1",
            "org.apache.maven.plugins:maven-scm-plugin:2.2.1",
            "org.apache.maven.plugins:maven-wrapper-plugin:3.3.4",
        ],
    },
]
EXPECTED_REPOSITORY = "TommyKammy/Shirokuma"
EXPECTED_SOURCE_REPOSITORY = "https://github.com/trinodb/trino"
EXPECTED_TAG = "483"
EXPECTED_TAG_OBJECT = "32d4f28e8311ea6f67edca209df59a0493d869fa"
EXPECTED_COMMIT = "50b0b50b75abd47f830b7805ee1b51716eb4065e"
EXPECTED_TREE = "3b5414292a614b12393bb4605ea2d4c588a5b8ee"
EXPECTED_BUILDER = (
    "docker.io/library/maven@"
    "sha256:7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c"
)
EXPECTED_REPOSITORIES = {
    "central": "https://repo.maven.apache.org/maven2/",
    "confluent": "https://packages.confluent.io/maven/",
}
EXPECTED_TRIVY_ROOTFS_OMISSIONS = [
    {
        "path": (
            "com/squareup/okhttp3/logging-interceptor/5.4.0/"
            "logging-interceptor-5.4.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/com.squareup.okhttp3/logging-interceptor@5.4.0"
            "?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "com/squareup/okhttp3/okhttp-java-net-cookiejar/5.4.0/"
            "okhttp-java-net-cookiejar-5.4.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/com.squareup.okhttp3/okhttp-java-net-cookiejar@5.4.0"
            "?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "com/squareup/okhttp3/okhttp-jvm/5.4.0/"
            "okhttp-jvm-5.4.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/com.squareup.okhttp3/okhttp-jvm@5.4.0"
            "?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "com/squareup/okhttp3/okhttp-urlconnection/5.4.0/"
            "okhttp-urlconnection-5.4.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/com.squareup.okhttp3/okhttp-urlconnection@5.4.0"
            "?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "com/squareup/okio/okio-jvm/3.17.0/"
            "okio-jvm-3.17.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/com.squareup.okio/okio-jvm@3.17.0"
            "?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "dev/failsafe/failsafe/3.3.2/"
            "failsafe-3.3.2-sources.jar"
        ),
        "purl": "pkg:maven/dev.failsafe/failsafe@3.3.2?classifier=sources",
        "role": "supplemental-sources",
    },
    {
        "path": "dev/failsafe/failsafe/3.3.2/failsafe-3.3.2.jar",
        "purl": "pkg:maven/dev.failsafe/failsafe@3.3.2",
        "role": "base-coordinate",
    },
    {
        "path": (
            "io/opentelemetry/instrumentation/opentelemetry-okhttp-3.0/"
            "2.29.0-alpha/"
            "opentelemetry-okhttp-3.0-2.29.0-alpha-sources.jar"
        ),
        "purl": (
            "pkg:maven/io.opentelemetry.instrumentation/"
            "opentelemetry-okhttp-3.0@2.29.0-alpha?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "org/apache/iceberg/iceberg-core/1.11.0/"
            "iceberg-core-1.11.0-tests.jar"
        ),
        "purl": (
            "pkg:maven/org.apache.iceberg/iceberg-core@1.11.0"
            "?classifier=tests"
        ),
        "role": "supplemental-tests",
    },
    {
        "path": (
            "org/jetbrains/kotlin/kotlin-stdlib/2.4.0/"
            "kotlin-stdlib-2.4.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/org.jetbrains.kotlin/kotlin-stdlib@2.4.0"
            "?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
    {
        "path": (
            "org/jspecify/jspecify/1.0.0/"
            "jspecify-1.0.0-sources.jar"
        ),
        "purl": (
            "pkg:maven/org.jspecify/jspecify@1.0.0?classifier=sources"
        ),
        "role": "supplemental-sources",
    },
]
EXPECTED_BUN_INPUT = {
    "name": "bun-linux-aarch64",
    "version": "v1.3.14",
    "platform": "linux/arm64",
    "url": (
        "https://github.com/oven-sh/bun/releases/download/"
        "bun-v1.3.14/bun-linux-aarch64.zip"
    ),
    "sha256": "a27ffb63a8310375836e0d6f668ae17fa8d8d18b88c37c821c65331973a19a3b",
    "size": 35_700_603,
    "cache_path": "com/github/eirslett/bun/1.3.14/bun-1.3.14.zip",
    "origin_id": "shirokuma-bun-release",
    "independent_downloads": 2,
    "allowed_https_origins": [
        "https://github.com",
        "https://release-assets.githubusercontent.com",
    ],
    "redirect_policy": "manual_validate_before_request",
    "maximum_redirects": 5,
}
EXPECTED_PARQUET_SOURCE_REMEDIATION = {
    "name": "parquet-jackson-source-remediation",
    "coordinate": "org.apache.parquet:parquet-jackson:1.17.1",
    "repository": "https://github.com/apache/parquet-java",
    "release_tag": "apache-parquet-1.17.1",
    "release_tag_object": "1f54ba44afb285fecbaf54bde5c0afa259327fc4",
    "nested_rc_tag": "apache-parquet-1.17.1-rc0",
    "nested_rc_tag_object": "172d200a7eb81161345bdccaf628af34178fc479",
    "commit_sha": "78a8d3230eb4769db93de5f2f2e18363c04cae81",
    "tree_sha": "28b877df95a7a661361b8776f6ebe21d73d8da6d",
    "permitted_paths": ["pom.xml"],
    "preimage": {
        "path": "pom.xml",
        "size": 24_493,
        "sha256": (
            "bfe7519b9886e9df51bfef8be52064b3aadcbf9ae21c77402d8a66837aa5442f"
        ),
    },
    "replacements": [
        {
            "from": "<jackson.version>2.21.3</jackson.version>",
            "to": "<jackson.version>2.21.4</jackson.version>",
        },
        {
            "from": (
                "<jackson-databind.version>2.21.3</jackson-databind.version>"
            ),
            "to": (
                "<jackson-databind.version>2.21.4</jackson-databind.version>"
            ),
        },
    ],
    "postimage": {
        "path": "pom.xml",
        "size": 24_493,
        "sha256": (
            "e07982c0f114b592c06c2aba1254df9c280b69a2dd27f3a0739421fe84d12efa"
        ),
    },
    "output_timestamp": "2026-05-08T01:45:35Z",
    "independent_source_fetches": 2,
    "independent_builds": 2,
    "byte_identical_outputs_required": True,
    "origin_id": "shirokuma-parquet-remediation",
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5105612399"
    ),
    "expires_at": "2026-08-21T22:43:36Z",
    "automatic_renewal": False,
}
EXPECTED_SCM_METADATA_REMEDIATION = {
    "name": "maven-scm-reviewed-metadata-remediation",
    "repository": "https://github.com/TommyKammy/Shirokuma",
    "origin_id": "shirokuma-scm-remediation",
    "files": [
        {
            "path": (
                "org/apache/maven/scm/maven-scm-provider-gitexe/2.2.1/"
                "maven-scm-provider-gitexe-2.2.1.pom"
            ),
            "size": 2_720,
            "sha256": (
                "0652487bb3cd532ce6ba9fd841c7f2346c1192b3271996a06ddd50f3052186a6"
            ),
        },
        {
            "path": (
                "org/apache/maven/scm/maven-scm-provider-gitexe/2.2.1/"
                "maven-scm-provider-gitexe-2.2.1.pom.sha1"
            ),
            "size": 40,
            "sha256": (
                "a9a85b2193053267f68dfacb62896caa532afe49bafa4540134df7a6abed5beb"
            ),
        },
        {
            "path": (
                "org/apache/maven/scm/maven-scm-manager-plexus/2.2.1/"
                "maven-scm-manager-plexus-2.2.1.pom"
            ),
            "size": 1_957,
            "sha256": (
                "4e7b25d9f3dfd21b874593edf794270888c8ef13bc29394b0da1c1cbefa41c43"
            ),
        },
        {
            "path": (
                "org/apache/maven/scm/maven-scm-manager-plexus/2.2.1/"
                "maven-scm-manager-plexus-2.2.1.pom.sha1"
            ),
            "size": 40,
            "sha256": (
                "8f04dcac652c18121420956ca62c7efb0166eefaa24400129d2a01433133de63"
            ),
        },
    ],
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5210182460"
    ),
    "expires_at": "2026-08-21T22:43:36Z",
    "automatic_renewal": False,
}
EXPECTED_PARQUET_REMEDIATION_JAR_PATH = (
    "org/apache/parquet/parquet-jackson/1.17.1/"
    "parquet-jackson-1.17.1.jar"
)
EXPECTED_BUN_PACKAGE_CACHE = {
    "bun_version": "v1.3.14",
    "platform": "linux/arm64",
    "cache_directory": "/bun-cache",
    "registry": "https://registry.npmjs.org/",
    "frozen_lockfiles": [
        {
            "path": "core/trino-web-ui/src/main/resources/webapp/bun.lock",
            "sha256": "90bfa0a797ae2f37a4ab5e8b445a62fa211328cedab186ac8c2402f78a07a194",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/"
                "webapp-legacy/src/bun.lock"
            ),
            "sha256": "c8c9044cf3bd90d38e5aab71e680bf0dd3724c4062eaac9a60e981629601a163",
        },
    ],
    "independent_reconstructions": 2,
    "reviewed_snapshot": {
        "manifest_sha256": (
            "ca5cc4e1a565ecdd7d3f29610b1cdbe869288357ae6e324c83de1a485872b453"
        ),
        "archive_sha256": (
            "863e9d08bf8d7f106059feff7a5e6d96ca7da17b2cf633381241fe66ab88b1ca"
        ),
        "archive_size": 128_430_898,
    },
    "network_none_rebuild_mount": "read-only",
    "network_none_cache_outside_source": True,
    "post_build_integrity_verification": True,
    "unknown_registry_permitted": False,
}
EXPECTED_BUN_SCAN_RESULTS = {
    "core/trino-web-ui/src/main/resources/webapp/bun.lock": {
        "package_count": 470,
        "required_packages": frozenset(
            {
                "@dagrejs/dagre",
                "@mui/material",
            }
        ),
    },
    "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock": {
        "package_count": 299,
        "required_packages": frozenset(
            {
                "dagre-d3",
                "reactable",
            }
        ),
    },
}
EXPECTED_SOURCE_OVERLAY = {
    "state": "approved_bounded_web_ui_security",
    "decision_record": OVERLAY_ADR_PATH.as_posix(),
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5081842992"
    ),
    "expires_at": "2026-08-21T22:43:36Z",
    "automatic_renewal": False,
    "applied_after_source_verification": True,
    "patch": {
        "path": SOURCE_OVERLAY_PATH.as_posix(),
        "sha256": "90a2168b9e158f3a61e3cfe3c4e21b6cc6df39c393c2bb38a6df3ffa2e7a3d66",
    },
    "apply_arguments": [
        "--unidiff-zero",
        "--whitespace=error-all",
    ],
    "permitted_paths": [
        "core/trino-web-ui/src/main/resources/webapp/package.json",
        "core/trino-web-ui/src/main/resources/webapp/bun.lock",
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/package.json",
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock",
    ],
    "preimages": {
        "core/trino-web-ui/src/main/resources/webapp/package.json": (
            "0e059ceb7d558961bfafc93cb1f34ad4aebbc28caa6ccbc62e4635bf4f9e44e9"
        ),
        "core/trino-web-ui/src/main/resources/webapp/bun.lock": (
            "70da1dad7c6f45743637cba7dde948793d787b1ced1382e90966d60fe17dc885"
        ),
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/package.json": (
            "d241303ae65fa0d79ada35538e6948a3e8bdcc96b1bf132cab0d9d87a50c1c60"
        ),
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock": (
            "0ca8b926ea0a2af3fff339b43c52de03a8f99c4aa9ba1d4c2ecd081bcd715ad3"
        ),
    },
    "postimages": {
        "core/trino-web-ui/src/main/resources/webapp/package.json": (
            "2af750b9571eddce309373325779a3e2d35ba1977db25b657fe4337f3cb687d2"
        ),
        "core/trino-web-ui/src/main/resources/webapp/bun.lock": (
            "90bfa0a797ae2f37a4ab5e8b445a62fa211328cedab186ac8c2402f78a07a194"
        ),
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/package.json": (
            "d555ca5ab130a76775c9677b5b9798dee486964ef0fc4992fbb02705e696d271"
        ),
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock": (
            "c8c9044cf3bd90d38e5aab71e680bf0dd3724c4062eaac9a60e981629601a163"
        ),
    },
    "dependency_overrides": {
        "brace-expansion": "5.0.9",
        "d3-color": "3.1.0",
        "fast-uri": "3.1.5",
        "js-yaml": "4.3.1",
        "nanoid": "3.3.17",
        "postcss": "8.5.18",
        "react-router-dom": "7.18.1",
    },
    "react_router_import_inventory": [
        {
            "path": "core/trino-web-ui/src/main/resources/webapp/src/App.tsx",
            "statement": (
                "import { HashRouter as Router, Routes, Route, Navigate } "
                "from 'react-router-dom'"
            ),
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/Layout.tsx"
            ),
            "statement": "import { useLocation, Link } from 'react-router-dom'",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/MetricCard.tsx"
            ),
            "statement": "import { Link as RouterLink } from 'react-router-dom'",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/QueryDetails.tsx"
            ),
            "statement": (
                "import { useParams, useSearchParams } from 'react-router-dom'"
            ),
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/QueryListItem.tsx"
            ),
            "statement": "import { Link as RouterLink } from 'react-router-dom'",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/QueryStageCard.tsx"
            ),
            "statement": "import { Link as RouterLink } from 'react-router'",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/WorkerStatus.tsx"
            ),
            "statement": "import { useParams } from 'react-router-dom'",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/"
                "components/WorkersList.tsx"
            ),
            "statement": "import { Link as RouterLink } from 'react-router-dom'",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/webapp/src/router.tsx"
            ),
            "statement": "import { RouteProps } from 'react-router-dom'",
        },
    ],
    "vulnerability_assessment": {
        "raw_report_required": True,
        "adjusted_report_required": True,
        "raw_finding": {
            "target": "core/trino-web-ui/src/main/resources/webapp/bun.lock",
            "vulnerability_id": "GHSA-qwww-vcr4-c8h2",
            "package": "react-router",
            "installed_version": "7.18.1",
            "fixed_version": "7.18.2, 8.3.0",
            "severity": "HIGH",
            "purl": "pkg:npm/react-router@7.18.1",
        },
        "openvex": {
            "path": VEX_PATH.as_posix(),
            "sha256": (
                "f36e8c7ab98f177c0e3f796a22cfa23e8709dc9a9b39bff32806c9b5db534a2a"
            ),
            "status": "not_affected",
            "justification": "vulnerable_code_not_in_execute_path",
        },
        "adjusted_maximum_high": 0,
        "adjusted_maximum_critical": 0,
        "raw_and_adjusted_package_inventory_must_match": True,
    },
}
EXPECTED_DISTRIBUTION_REMEDIATION = {
    "state": "approved_bounded_iceberg_only_maven_closure",
    "decision_record": DISTRIBUTION_REMEDIATION_ADR_PATH.as_posix(),
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5115851323"
    ),
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "approved_at": "2026-07-29T09:35:05Z",
    "expires_at": "2026-08-21T22:43:36Z",
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "implementation_author": "Codex",
    "reviewer_must_differ_from_implementation_author": True,
    "source_binding": {
        "repository": EXPECTED_SOURCE_REPOSITORY,
        "release_tag": EXPECTED_TAG,
        "commit_sha": EXPECTED_COMMIT,
        "tree_sha": EXPECTED_TREE,
    },
    "patch": {
        "path": DISTRIBUTION_REMEDIATION_PATH.as_posix(),
        "sha256": "dd9cd76984c4bd2845aa95e87cdb404f7d24c0cfed65d5a780da32ce4f9d4269",
    },
    "apply_arguments": ["--unidiff-zero", "--whitespace=error-all"],
    "permitted_paths": [
        "pom.xml",
        "core/trino-spi/pom.xml",
        "core/trino-server-core/src/main/provisio/trino-core.xml",
        "core/trino-server/src/main/provisio/trino.xml",
    ],
    "preimages": {
        "pom.xml": (
            "e1ba9a61315097e3a7133238c778ec161ac6097fe77a660fc5455a3e84568820"
        ),
        "core/trino-spi/pom.xml": (
            "9a3ab7c1e730e9534ca575b243865f4ff8ca355d201e5a7aa79f244401806993"
        ),
        "core/trino-server-core/src/main/provisio/trino-core.xml": (
            "0f2e86c7cb0873c43a602a55e8c8827bc3292fbe09868014ca360b61179d6863"
        ),
        "core/trino-server/src/main/provisio/trino.xml": (
            "ca8b95cdd6579da16fe531c2110f5c4d67e63f385b37b5b7ab9a220bee58c323"
        ),
    },
    "postimages": {
        "pom.xml": (
            "8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1"
        ),
        "core/trino-spi/pom.xml": (
            "3032163467da8247367e3c0ac60d790ddabc96c632c083448d5b5a7d63f05b2b"
        ),
        "core/trino-server-core/src/main/provisio/trino-core.xml": (
            "585f0b68b6e0c2b1da66f71a0e289b77e776fca3e0451a17d55f35b10e18727a"
        ),
        "core/trino-server/src/main/provisio/trino.xml": (
            "f549d66db97d1bbee1b2505b6b3875ca3db9362a88b0d7402de8cc921bd5c018"
        ),
    },
    "output_timestamp": "2026-07-18T00:36:39Z",
    "selected_projects": [
        ":trino-server",
        ":trino-server-core",
        ":trino-server-main",
        ":trino-hdfs",
        ":trino-iceberg",
    ],
    "also_make_required_projects": True,
    "distribution_contents": {
        "server_modules": ["trino-server-core", "trino-server-main"],
        "plugins": ["iceberg"],
        "iceberg_runtime_dependencies": ["trino-hdfs"],
        "other_plugins_permitted": False,
    },
    "dependency_replacements": {
        "com.fasterxml.jackson.core:jackson-core": "2.21.4",
        "com.fasterxml.jackson.core:jackson-databind": "2.21.4",
        "commons-beanutils:commons-beanutils": "1.11.0",
        "commons-io:commons-io": "2.22.0",
        "org.apache.maven:maven-core": "3.9.16",
        "org.codehaus.plexus:plexus-archiver": "4.12.0",
        "org.codehaus.plexus:plexus-utils": ["3.6.1", "4.0.3"],
        "com.github.eirslett:frontend-maven-plugin": "2.0.2",
        "ca.vanzyl.provisio.maven.plugins:provisio-maven-plugin": "2.0.0",
        "org.apache.maven.plugins:maven-jar-plugin": "3.5.1",
    },
    "independent_reconstructions_required": 2,
    "network_none_rebuilds_required": 2,
    "byte_identical_outputs_required": True,
    "high_zero_critical_zero_required": True,
    "vulnerability_waiver_permitted": False,
    "expiry_action": (
        "fail_closed_before_source_execution_dependency_resolution_or_publication"
    ),
}
EXPECTED_BUILD_PLUGIN_REMEDIATION = {
    "state": "approved_exact_maven_build_plugin_closure",
    "decision_record": BUILD_PLUGIN_REMEDIATION_ADR_PATH.as_posix(),
    "approval_record": (
        "https://github.com/TommyKammy/Shirokuma/issues/63"
        "#issuecomment-5210182460"
    ),
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "approved_at": "2026-08-07T00:08:48Z",
    "expires_at": "2026-08-21T22:43:36Z",
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "implementation_author": "Codex",
    "reviewer_must_differ_from_implementation_author": True,
    "reviewer_must_differ_from_risk_owner": True,
    "approval_must_match_final_head_sha": True,
    "source_binding": {
        "repository": EXPECTED_SOURCE_REPOSITORY,
        "release_tag": EXPECTED_TAG,
        "commit_sha": EXPECTED_COMMIT,
        "tree_sha": EXPECTED_TREE,
    },
    "patch": {
        "path": BUILD_PLUGIN_REMEDIATION_PATH.as_posix(),
        "sha256": (
            "731e76f296a725d34ea9e226a1815782168cae3890424e69f76a05530afc15be"
        ),
    },
    "apply_arguments": ["--unidiff-zero", "--whitespace=error-all"],
    "permitted_paths": ["pom.xml"],
    "preimages": {
        "pom.xml": (
            "8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1"
        ),
    },
    "postimages": {
        "pom.xml": (
            "871c6b21cf9fc70c455d21b64d24dd4501a8b5943242418edc2b2f5cfe14fab8"
        ),
    },
    "replacement_inputs": [
        (
            "org/apache/velocity/velocity-engine-core/2.4.1/"
            "velocity-engine-core-2.4.1.jar"
        ),
        (
            "org/codehaus/plexus/plexus-utils/4.0.3/"
            "plexus-utils-4.0.3.jar"
        ),
    ],
    "removed_build_extension": (
        "io.github.gitflow-incremental-builder:"
        "gitflow-incremental-builder:4.6.0"
    ),
    "hardened_maven_metadata": [
        {
            "path": (
                "org/apache/maven/scm/maven-scm-provider-gitexe/2.2.1/"
                "maven-scm-provider-gitexe-2.2.1.pom"
            ),
            "sha256": (
                "0652487bb3cd532ce6ba9fd841c7f2346c1192b3271996a06ddd50f3052186a6"
            ),
            "sha1": "a8630355e52d9c81dbd6ec117820bb58b6355f4a",
        },
        {
            "path": (
                "org/apache/maven/scm/maven-scm-manager-plexus/2.2.1/"
                "maven-scm-manager-plexus-2.2.1.pom"
            ),
            "sha256": (
                "4e7b25d9f3dfd21b874593edf794270888c8ef13bc29394b0da1c1cbefa41c43"
            ),
            "sha1": "eb1b7ab169dc923806b0040631a45dc83d0b83e8",
        },
    ],
    "selected_projects": [
        ":trino-server",
        ":trino-server-core",
        ":trino-server-main",
        ":trino-hdfs",
        ":trino-iceberg",
    ],
    "feasibility_evidence": {
        "workflow_run": (
            "https://github.com/TommyKammy/Shirokuma/actions/runs/31072144404"
        ),
        "reviewed_commit": "864da7ea3eb93c976691836630cb91775d5074af",
        "workflow_execution_commit": (
            "f404219ce0a189336dbcc2a48fd881064edc3780"
        ),
        "artifact_id": 8956062532,
        "artifact_digest": (
            "sha256:46c1ea610e0cb24df43b93cd954c05222573562f9b1677d3d8f7fd0ba65814da"
        ),
        "artifact_expires_at": "2026-09-05T04:48:07Z",
        "validation_record": AUTHORIZED_FEASIBILITY_RECORD_PATH.as_posix(),
        "artifact_receipt": AUTHORIZED_FEASIBILITY_RECEIPT_PATH.as_posix(),
        "archive_sha256": (
            "b12debf4e760e3042fe34bd9946c2ed96d0ead4eb7959d8491fe63f3240c208a"
        ),
        "archive_bytes": 242_729_683,
        "manifest_sha256": (
            "6667d7b8275c0b24ec4f8ec7070173a57a888ab425d8697e0b261f49fc347ea4"
        ),
        "manifest_file_count": 5_036,
        "independent_reaudit": "passed_complete_hardened_feasibility",
    },
    "high_zero_critical_zero_required": True,
    "vulnerability_waiver_permitted": False,
    "expiry_action": (
        "fail_closed_before_source_execution_dependency_resolution_or_publication"
    ),
}
EXPECTED_ADMISSION_OVERLAY_AUTHORIZATION = {
    "status": "active",
    "authorization_type": (
        "time_boxed_bounded_source_overlay_and_not_affected_assessment"
    ),
    "decision_record": OVERLAY_ADR_PATH.as_posix(),
    "approval_record": EXPECTED_SOURCE_OVERLAY["approval_record"],
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "expires_at": EXPECTED_SOURCE_OVERLAY["expires_at"],
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "implementation_author": "Codex",
    "reviewer_must_differ_from_implementation_author": True,
    "source_binding": {
        "repository": EXPECTED_SOURCE_REPOSITORY,
        "release_tag": EXPECTED_TAG,
        "commit_sha": EXPECTED_COMMIT,
        "tree_sha": EXPECTED_TREE,
    },
    "permitted_paths": EXPECTED_SOURCE_OVERLAY["permitted_paths"],
    "openvex_scope": {
        "vulnerability_id": "GHSA-qwww-vcr4-c8h2",
        "product": "pkg:npm/react-router@7.18.1",
        "status": "not_affected",
        "justification": "vulnerable_code_not_in_execute_path",
    },
    "vulnerability_risk_accepted": False,
    "raw_finding_retention_required": True,
    "adjusted_high_zero_critical_zero_required": True,
    "expiry_action": "fail_closed_before_dependency_resolution_or_publication",
}
EXPECTED_SOURCE_REMEDIATION = {
    "state": "approved_bounded_parquet_jackson_1_17_1",
    "decision_record": PARQUET_REMEDIATION_ADR_PATH.as_posix(),
    "approval_record": EXPECTED_PARQUET_SOURCE_REMEDIATION["approval_record"],
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "expires_at": EXPECTED_PARQUET_SOURCE_REMEDIATION["expires_at"],
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "implementation_author": "Codex",
    "reviewer_must_differ_from_implementation_author": True,
    "input": EXPECTED_PARQUET_SOURCE_REMEDIATION,
    "outputs": [
        (
            "org/apache/parquet/parquet-jackson/1.17.1/"
            "parquet-jackson-1.17.1.jar"
        ),
        (
            "org/apache/parquet/parquet-jackson/1.17.1/"
            "parquet-jackson-1.17.1.pom"
        ),
    ],
    "high_zero_critical_zero_required": True,
    "vulnerability_waiver_permitted": False,
    "expiry_action": (
        "fail_closed_before_source_execution_dependency_resolution_or_publication"
    ),
}
EXPECTED_ADMISSION_SOURCE_REMEDIATION_AUTHORIZATION = {
    "status": "active",
    "authorization_type": (
        "time_boxed_bounded_parquet_jackson_source_remediation"
    ),
    "decision_record": PARQUET_REMEDIATION_ADR_PATH.as_posix(),
    "approval_record": EXPECTED_PARQUET_SOURCE_REMEDIATION["approval_record"],
    "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
    "expires_at": EXPECTED_PARQUET_SOURCE_REMEDIATION["expires_at"],
    "automatic_renewal": False,
    "risk_owner": "TommyKammy",
    "implementation_author": "Codex",
    "reviewer_must_differ_from_implementation_author": True,
    "source_binding": {
        key: EXPECTED_PARQUET_SOURCE_REMEDIATION[key]
        for key in (
            "repository",
            "release_tag",
            "release_tag_object",
            "nested_rc_tag",
            "nested_rc_tag_object",
            "commit_sha",
            "tree_sha",
        )
    },
    "permitted_paths": ["pom.xml"],
    "preimage_sha256": EXPECTED_PARQUET_SOURCE_REMEDIATION["preimage"][
        "sha256"
    ],
    "postimage_sha256": EXPECTED_PARQUET_SOURCE_REMEDIATION["postimage"][
        "sha256"
    ],
    "dependency_replacements": {
        "jackson": "2.21.3 -> 2.21.4",
        "jackson-databind": "2.21.3 -> 2.21.4",
    },
    "independent_source_fetches_required": 2,
    "independent_builds_required": 2,
    "byte_identical_outputs_required": True,
    "vulnerability_risk_accepted": False,
    "high_zero_critical_zero_required": True,
    "expiry_action": (
        "fail_closed_before_source_execution_dependency_resolution_or_publication"
    ),
}
EXPECTED_ADMISSION_DISTRIBUTION_REMEDIATION_AUTHORIZATION = {
    "status": "active",
    "authorization_type": (
        "time_boxed_bounded_iceberg_only_maven_closure_remediation"
    ),
    **{
        key: EXPECTED_DISTRIBUTION_REMEDIATION[key]
        for key in (
            "decision_record",
            "approval_record",
            "issue",
            "approved_at",
            "expires_at",
            "automatic_renewal",
            "risk_owner",
            "implementation_author",
            "reviewer_must_differ_from_implementation_author",
            "source_binding",
            "patch",
            "permitted_paths",
            "preimages",
            "postimages",
            "output_timestamp",
            "selected_projects",
            "also_make_required_projects",
            "distribution_contents",
            "dependency_replacements",
            "independent_reconstructions_required",
            "network_none_rebuilds_required",
            "byte_identical_outputs_required",
            "high_zero_critical_zero_required",
            "vulnerability_waiver_permitted",
            "expiry_action",
        )
    },
}
EXPECTED_ADMISSION_BUILD_PLUGIN_REMEDIATION_AUTHORIZATION = {
    "status": "active",
    "authorization_type": "time_boxed_exact_maven_build_plugin_closure",
    **{
        key: EXPECTED_BUILD_PLUGIN_REMEDIATION[key]
        for key in (
            "decision_record",
            "approval_record",
            "issue",
            "approved_at",
            "expires_at",
            "automatic_renewal",
            "risk_owner",
            "implementation_author",
            "reviewer_must_differ_from_implementation_author",
            "reviewer_must_differ_from_risk_owner",
            "approval_must_match_final_head_sha",
            "source_binding",
            "patch",
            "permitted_paths",
            "preimages",
            "postimages",
            "replacement_inputs",
            "removed_build_extension",
            "selected_projects",
            "feasibility_evidence",
            "high_zero_critical_zero_required",
            "vulnerability_waiver_permitted",
            "expiry_action",
        )
    },
}
EXPECTED_PARQUET_SLSA_RESOLVED_DEPENDENCY = {
    "claim_path": "predicate.buildDefinition.resolvedDependencies",
    "required_uri": (
        "git+https://github.com/apache/parquet-java"
        "@refs/tags/apache-parquet-1.17.1"
    ),
    "required_digest": {
        "gitTagObject": "1f54ba44afb285fecbaf54bde5c0afa259327fc4",
        "gitNestedTagObject": "172d200a7eb81161345bdccaf628af34178fc479",
        "gitCommit": "78a8d3230eb4769db93de5f2f2e18363c04cae81",
        "gitTree": "28b877df95a7a661361b8776f6ebe21d73d8da6d",
        "sourcePreimageSha256": (
            "bfe7519b9886e9df51bfef8be52064b3aadcbf9ae21c77402d8a66837aa5442f"
        ),
        "sourcePostimageSha256": (
            "e07982c0f114b592c06c2aba1254df9c280b69a2dd27f3a0739421fe84d12efa"
        ),
    },
    "exactly_one_matching_descriptor_required": True,
    "source_checkout_must_match_descriptor": True,
}
EXPECTED_TRINO_BUILD_EXTENSION = {
    "group_id": "io.trino",
    "artifact_id": "trino-maven-plugin",
    "version": "20",
    "repository_origin": EXPECTED_REPOSITORIES["central"],
    "required_files": [
        "trino-maven-plugin-20.jar",
        "trino-maven-plugin-20.pom",
    ],
    "reactor_output": False,
}
EXPECTED_TRINO_EXTERNAL_MAVEN_INPUTS = {
    "repository_origin": EXPECTED_REPOSITORIES["central"],
    "required_paths": [
        "io/trino/coral/coral/2.2.49-1/coral-2.2.49-1.jar",
        "io/trino/coral/coral/2.2.49-1/coral-2.2.49-1.pom",
        "io/trino/hadoop/hadoop-apache/3.3.5-3/hadoop-apache-3.3.5-3.jar",
        "io/trino/hadoop/hadoop-apache/3.3.5-3/hadoop-apache-3.3.5-3.pom",
        "io/trino/hive/hive-apache/3.1.2-23/hive-apache-3.1.2-23.jar",
        "io/trino/hive/hive-apache/3.1.2-23/hive-apache-3.1.2-23.pom",
        "io/trino/hive/hive-thrift/3/hive-thrift-3.jar",
        "io/trino/hive/hive-thrift/3/hive-thrift-3.pom",
        "io/trino/tempto/tempto-core/204/tempto-core-204.jar",
        "io/trino/tempto/tempto-core/204/tempto-core-204.pom",
        "io/trino/tempto/tempto-root/204/tempto-root-204.pom",
        "io/trino/tpcds/tpcds/1.7/tpcds-1.7.jar",
        "io/trino/tpcds/tpcds/1.7/tpcds-1.7.pom",
        "io/trino/tpch/tpch/1.4/tpch-1.4.jar",
        "io/trino/tpch/tpch/1.4/tpch-1.4.pom",
        "io/trino/trino-maven-plugin/20/trino-maven-plugin-20.jar",
        "io/trino/trino-maven-plugin/20/trino-maven-plugin-20.pom",
        "io/trino/trino-re2j/1.7/trino-re2j-1.7.jar",
        "io/trino/trino-re2j/1.7/trino-re2j-1.7.pom",
    ],
    "unknown_paths_permitted": False,
    "reactor_output": False,
}
EXPECTED_ARTIFACT_TYPE = "application/vnd.shirokuma.trino.build-dependencies.v3"
EXPECTED_DESCRIPTOR_MEDIA_TYPE = (
    "application/vnd.shirokuma.maven-dependency-manifest.v2+json"
)
EXPECTED_BUN_DESCRIPTOR_MEDIA_TYPE = (
    "application/vnd.shirokuma.bun-dependency-manifest.v1+json"
)
EXPECTED_BUN_ARCHIVE_MEDIA_TYPE = (
    "application/vnd.shirokuma.bun-cache.v1.tar+gzip"
)
EXPECTED_BUN_STAGE_BLOCK = """\
          python3 scripts/prepare_trino_bun_input.py download \\
            --url "${BUN_URL}" \\
            --archive "${bun_archive}"
          test "$(stat --format='%s' "${bun_archive}")" = "${BUN_ARCHIVE_SIZE}"
          echo "${BUN_ARCHIVE_SHA256}  ${bun_archive}" \\
            | sha256sum --check --strict
          python3 scripts/prepare_trino_bun_input.py stage \\
            --archive "${bun_archive}" \\
            --repository "${repository}"
"""
EXPECTED_PR_SOURCE_CONDITION = """\
        if: >-
          steps.lifecycle.outputs.active == 'true' ||
          steps.lifecycle.outputs.source_validation_active == 'true'
"""
EXPECTED_PR_BUN_INPUT_BLOCK = """\
          bun_archive="${RUNNER_TEMP}/bun-linux-aarch64-pr.zip"
          bun_dir="${RUNNER_TEMP}/trino-pr-bun"
          install -d -m 0700 "${bun_dir}" "${BUN_INSTALL_CACHE_DIR}"
          python3 scripts/prepare_trino_bun_input.py download \\
            --url "${BUN_URL}" \\
            --archive "${bun_archive}"
          python3 scripts/prepare_trino_bun_input.py verify \\
            --archive "${bun_archive}"
          unzip -p "${bun_archive}" bun-linux-aarch64/bun \\
            > "${bun_dir}/bun"
          chmod 0500 "${bun_dir}/bun"
          test "$("${bun_dir}/bun" --version)" = "1.3.14"
"""
EXPECTED_PR_OVERLAY_BUILD_MARKERS = (
    '              echo "active=false" >> "${GITHUB_OUTPUT}"',
    (
        '                echo "source_validation_active=false" >> '
        '"${GITHUB_OUTPUT}"'
    ),
    (
        '                echo "Pull requests perform static contract validation '
        'only while publication is blocked"'
    ),
    (
        '              echo "source_validation_active=true" >> '
        '"${GITHUB_OUTPUT}"'
    ),
    (
        '              echo "Pull requests perform static and Web UI overlay '
        'build validation only"'
    ),
    "        if: steps.lifecycle.outputs.source_validation_active == 'true'",
    '          CI: "true"',
    "          BUN_CONFIG_REGISTRY: ${{ env.BUN_REGISTRY }}",
    "          BUN_INSTALL_CACHE_DIR: ${{ runner.temp }}/trino-pr-bun-cache",
    '          bun install --frozen-lockfile --cwd "${modern}"',
    '          bun run --cwd "${modern}" typecheck',
    '          bun run --cwd "${modern}" build',
    '          bun run --cwd "${legacy}" package:clean',
)
EXPECTED_BUN_SCAN_STAGE_BLOCK = """\
      - name: Stage the exact Bun lockfiles for dependency analysis
        if: steps.lifecycle.outputs.active == 'true'
        id: stage_bun_scan_input
        shell: bash
        run: |
"""
EXPECTED_BUN_ADJUSTED_SCAN_REPORT_BLOCK = """\
      - name: Apply OpenVEX and retain adjusted Bun High or Critical findings
        if: >-
          always() &&
          steps.lifecycle.outputs.active == 'true' &&
          steps.stage_bun_scan_input.outcome == 'success'
        env:
          TRIVY_INCLUDE_DEV_DEPS: "true"
          TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy
        shell: bash
        run: |
          set -euo pipefail
          candidate="${GITHUB_WORKSPACE}/.trino-candidate"
          vex="${GITHUB_WORKSPACE}/bootstrap/trino/v483/vex/react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json"
          install -m 0444 "${vex}" \\
            "${candidate}/react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json"
          trivy fs \\
            --scanners vuln \\
            --severity HIGH,CRITICAL \\
            --ignore-unfixed=false \\
            --skip-db-update \\
            --pkg-types library \\
            --exit-code 0 \\
            --list-all-pkgs \\
            --format json \\
            --output "${candidate}/trivy-bun-vulnerability.json" \\
            --vex "${vex}" \\
            "${RUNNER_TEMP}/trino-bun-scan-input"
"""
EXPECTED_BUN_SCAN_GATE_BLOCK = """\
      - name: Verify and block the raw and OpenVEX-adjusted Bun dependency evidence
        if: >-
          always() &&
          steps.lifecycle.outputs.active == 'true' &&
          steps.stage_bun_scan_input.outcome == 'success'
        id: verify_bun_scan
        shell: bash
        run: |
          set -euo pipefail
          python3 scripts/verify_trino_dependency_publisher.py \\
            verify-bun-scan \\
            --root . \\
            --scan-input "${RUNNER_TEMP}/trino-bun-scan-input" \\
            --raw-report \\
              "${GITHUB_WORKSPACE}/.trino-candidate/trivy-bun-vulnerability-raw.json" \\
            --adjusted-report \\
              "${GITHUB_WORKSPACE}/.trino-candidate/trivy-bun-vulnerability.json"
"""
EXPECTED_BUN_FAILURE_DIAGNOSTIC_QUARTET_BLOCK = """\
      - name: Verify complete failed Bun vulnerability diagnostic quartet
        if: >-
          failure() &&
          steps.lifecycle.outputs.active == 'true' &&
          steps.verify_bun_scan.outcome == 'failure'
        id: verify_bun_diagnostic_quartet
        shell: bash
        run: |
          set -euo pipefail
          candidate="${GITHUB_WORKSPACE}/.trino-candidate"
          required=(
            "trino-bun-dependencies-483.cdx.json"
            "trivy-bun-vulnerability-raw.json"
            "trivy-bun-vulnerability.json"
            "react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json"
          )
          for filename in "${required[@]}"; do
            path="${candidate}/${filename}"
            if [[ ! -s "${path}" ]]; then
              printf 'missing or empty required Bun diagnostic: %s\\n' \\
                "${path}" >&2
              exit 1
            fi
          done
"""
EXPECTED_BUN_FAILURE_DIAGNOSTIC_BLOCK = """\
      - name: Retain failed Bun vulnerability diagnostics
        if: >-
          failure() &&
          steps.lifecycle.outputs.active == 'true' &&
          steps.verify_bun_scan.outcome == 'failure' &&
          steps.verify_bun_diagnostic_quartet.outcome == 'success'
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: >-
            trino-bun-vulnerability-diagnostics-${{ github.run_id }}-${{
            github.run_attempt }}
          include-hidden-files: true
          path: |
            .trino-candidate/trino-bun-dependencies-483.cdx.json
            .trino-candidate/trivy-bun-vulnerability-raw.json
            .trino-candidate/trivy-bun-vulnerability.json
            .trino-candidate/react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json
          if-no-files-found: error
          retention-days: 14
"""
EXPECTED_BUN_DIAGNOSTIC_ARTIFACT_PREFIX = (
    "trino-bun-vulnerability-diagnostics-"
)
EXPECTED_RECORD_TRIVY_CACHE_BLOCK = """\
      - name: Record the read-only candidate
        if: steps.lifecycle.outputs.active == 'true'
        id: record
        env:
          TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy
        shell: bash
        run: |
          set -euo pipefail
          candidate="${GITHUB_WORKSPACE}/.trino-candidate"
          trivy version --format json > "${candidate}/trivy-version.json"
"""
EXPECTED_MAVEN_SCAN_REPORT_BLOCK = """\
      - name: Scan the dependency closure and record High or Critical findings
        if: steps.lifecycle.outputs.active == 'true'
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          version: v0.72.0
          scan-type: sbom
          scan-ref: .trino-candidate/trino-maven-dependencies-483.cdx.json
          format: json
          output: .trino-candidate/trivy-vulnerability.json
          scanners: vuln
          severity: HIGH,CRITICAL
          ignore-unfixed: false
          vuln-type: library
          list-all-pkgs: true
          exit-code: 0
"""
EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK = """\
      - name: Retain failed Maven vulnerability diagnostics
        if: >-
          failure() &&
          steps.lifecycle.outputs.active == 'true' &&
          steps.verify_maven_scan.outcome == 'failure'
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: >-
            trino-maven-vulnerability-diagnostics-${{ github.run_id }}-${{
            github.run_attempt }}
          include-hidden-files: true
          path: |
            .trino-candidate/maven-dependency-manifest.json
            .trino-candidate/trino-maven-rootfs-483.cdx.json
            .trino-candidate/trino-maven-dependencies-483.cdx.json
            .trino-candidate/trivy-vulnerability.json
          if-no-files-found: error
          retention-days: 14
"""
EXPECTED_CANDIDATE_HIDDEN_UPLOAD_BLOCK = """\
      - name: Retain the read-only-verified candidate
        if: steps.lifecycle.outputs.active == 'true'
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: ${{ steps.record.outputs.candidate_artifact_name }}
          include-hidden-files: true
          path: |
"""
EXPECTED_CANDIDATE_DOWNLOAD_BLOCK = """\
      - name: Download the exact read-only-verified candidate
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: ${{ needs.validate.outputs.candidate_artifact_name }}
          path: ${{ runner.temp }}/trino-maven-candidate
"""
EXPECTED_ORAS_PUSH_BLOCK = """\
          (
            cd "${candidate}"
            oras push \\
              --artifact-type "${ARTIFACT_TYPE}" \\
              "${tagged_reference}" \\
              "maven-dependency-manifest.json:${DESCRIPTOR_MEDIA_TYPE}" \\
              "trino-maven-dependencies-483.tar.gz:${ARCHIVE_MEDIA_TYPE}" \\
              "bun-dependency-manifest.json:${BUN_DESCRIPTOR_MEDIA_TYPE}" \\
              "trino-bun-dependencies-483.tar.gz:${BUN_ARCHIVE_MEDIA_TYPE}" \\
              > "oras-push.txt"
          )
"""
EXPECTED_ORAS_DIGEST_VALIDATION_BLOCK = """\
          digest=$(oras resolve "${tagged_reference}")
          if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
            echo "ORAS did not return a lowercase sha256 digest" >&2
            exit 1
          fi
"""
EXPECTED_SLSA_STATEMENT_ATTESTATION_BLOCK = """\
          cosign attest-blob --yes \\
            --statement "${candidate}/slsa-provenance.json" \\
            --hash "${PUBLISHED_DIGEST#sha256:}" \\
            --bundle "${candidate}/cosign-provenance-bundle.json"
          cosign attach attestation \\
            --attestation "${candidate}/cosign-provenance-bundle.json" \\
            "${PUBLISHED_REFERENCE}"
"""
EXPECTED_PARQUET_SLSA_RESOLVED_DEPENDENCY_BLOCK = """\
                      {
                          "uri": (
                              "git+https://github.com/apache/parquet-java"
                              "@refs/tags/apache-parquet-1.17.1"
                          ),
                          "digest": {
                              "gitTagObject": os.environ[
                                  "PARQUET_RELEASE_TAG_OBJECT"
                              ],
                              "gitNestedTagObject": os.environ[
                                  "PARQUET_RC_TAG_OBJECT"
                              ],
                              "gitCommit": os.environ[
                                  "PARQUET_SOURCE_COMMIT"
                              ],
                              "gitTree": os.environ["PARQUET_SOURCE_TREE"],
                              "sourcePreimageSha256": (
                                  "bfe7519b9886e9df51bfef8be52064b3"
                                  "aadcbf9ae21c77402d8a66837aa5442f"
                              ),
                              "sourcePostimageSha256": (
                                  "e07982c0f114b592c06c2aba1254df9c"
                                  "280b69a2dd27f3a0739421fe84d12efa"
                              ),
                          },
                      },
"""
EXPECTED_REPOSITORY_MIRRORS = (
    (
        ("id", "shirokuma-central"),
        ("mirrorOf", "central"),
        ("name", "Pin the Central repository id to Maven Central"),
        ("url", EXPECTED_REPOSITORIES["central"]),
    ),
    (
        ("id", "shirokuma-confluent"),
        ("mirrorOf", "confluent"),
        ("name", "Pin the Confluent repository id to Confluent packages"),
        ("url", EXPECTED_REPOSITORIES["confluent"]),
    ),
    (
        ("id", "shirokuma-central-fallback"),
        ("mirrorOf", "*"),
        (
            "name",
            "Route every other repository id through Maven Central",
        ),
        ("url", EXPECTED_REPOSITORIES["central"]),
    ),
)
EXPECTED_SETTINGS_POLICY = {
    "repository_owned_settings_only": True,
    "user_settings_permitted": False,
    "ambient_maven_home_permitted": False,
    "extensions_permitted": False,
    "mirrors_permitted": True,
    "mirror_escape_hatch_permitted": False,
    "mirror_policy": "exact_repository_ids_and_catch_all_to_allowlisted_origins",
    "proxies_permitted": False,
    "credentials_permitted": False,
}
EXPECTED_SETTINGS_MOUNT = (
    '--volume "${GITHUB_WORKSPACE}/bootstrap/trino/v483/settings.xml:'
    '/policy/settings.xml:ro"'
)
EXPECTED_SETTINGS_ARGUMENT = "--settings /policy/settings.xml"
EXPECTED_OFFLINE_REPOSITORY_SETTINGS = {
    "path": SETTINGS_PATH.as_posix(),
    "container_path": "/policy/settings.xml",
    "mount": "read-only",
    "required_for_online_resolution": True,
    "required_for_network_none_rebuild": True,
    "purpose": (
        "preserve_reviewed_mirror_repository_ids_for_offline_"
        "version_range_metadata"
    ),
    "network_access_permitted_by_this_setting": False,
}
EXPECTED_OFFLINE_MAVEN_REPOSITORY = {
    "host_path_template": (
        "${RUNNER_TEMP}/trino-offline-maven-repository-${suffix}"
    ),
    "path": "/m2",
    "mount": "read-only",
    "outside_source_checkout_required": True,
    "initialization": (
        "extract_verified_snapshot_repository_to_empty_path_before_"
        "network_none_builder_start"
    ),
    "sole_dependency_repository": True,
    "maven_args": (
        "-Dmaven.repo.local=/m2 "
        "-Daether.syncContext.named.basedir.locksDir=/tmp/maven-locks"
    ),
    "resolver_lock_path": "/tmp/maven-locks",
    "resolver_lock_path_outside_repository_required": True,
    "lifecycle": "clean package",
    "install_phase_executed": False,
    "local_repository_writes_permitted": False,
    "ambient_cache_mounts_permitted": False,
    "ambient_home_permitted": False,
    "prebuild_manifest_must_equal_snapshot": True,
    "repository_mounted_read_only_for_entire_build": True,
}
EXPECTED_OFFLINE_MAVEN_REPOSITORY_ASSIGNMENT = (
    'offline_repository="${RUNNER_TEMP}/'
    'trino-offline-maven-repository-${suffix}"'
)
EXPECTED_OFFLINE_MAVEN_REPOSITORY_EXTRACTION = (
    '--extract-root "${offline_repository}"'
)
EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT = (
    '--volume "${offline_repository}:/m2:ro"'
)
EXPECTED_OFFLINE_RESOLVER_LOCK_ARGUMENT = (
    "-Daether.syncContext.named.basedir.locksDir=/tmp/maven-locks"
)
EXPECTED_OFFLINE_LIFECYCLE = "-am clean package -DskipTests"
EXPECTED_OFFLINE_MAVEN_REPOSITORY_PREPARATION = (
    "for suffix in a b; do\n"
    '  offline_source="${RUNNER_TEMP}/trino-offline-${suffix}"\n'
    '  offline_repository="${RUNNER_TEMP}/'
    'trino-offline-maven-repository-${suffix}"\n'
    '  offline_bun_cache="${RUNNER_TEMP}/'
    'trino-offline-bun-cache-${suffix}"\n'
    '  test ! -e "${offline_repository}"\n'
    "  git clone --no-local --no-checkout \\\n"
    '    "${RUNNER_TEMP}/trino-source" "${offline_source}"\n'
    '  git -C "${offline_source}" remote set-url origin '
    '"${SOURCE_REPOSITORY}"\n'
    '  git -C "${offline_source}" checkout --detach "${SOURCE_COMMIT}"\n'
    "  python3 scripts/verify_trino_dependency_publisher.py audit-source \\\n"
    '    --root . --checkout "${offline_source}"\n'
    "  python3 scripts/verify_trino_dependency_publisher.py \\\n"
    "    apply-source-overlay \\\n"
    '    --root . --checkout "${offline_source}"\n'
    "  python3 scripts/package_trino_maven_dependencies.py verify \\\n"
    '    --descriptor "${candidate}/maven-dependency-manifest.json" \\\n'
    '    --archive "${candidate}/trino-maven-dependencies-483.tar.gz" \\\n'
    '    --extract-root "${offline_repository}"\n'
    "  python3 scripts/package_trino_bun_dependencies.py verify \\\n"
    '    --descriptor "${candidate}/bun-dependency-manifest.json" \\\n'
    '    --archive "${candidate}/trino-bun-dependencies-483.tar.gz" \\\n'
    '    --extract-root "${offline_bun_cache}"\n'
)
EXPECTED_OFFLINE_BUN_CACHE = {
    "path": "/bun-cache",
    "registry": "https://registry.npmjs.org/",
    "lockfile_mode": "frozen",
    "mount": "read-only",
    "network_none_required": True,
    "absolute_cache_alias_target_prefix": "/bun-cache/",
    "ambient_cache_permitted": False,
    "unknown_registry_permitted": False,
}
EXPECTED_OFFLINE_COMPILER_DEBUG = {
    "maven_property": "maven.compiler.debuglevel",
    "value": "source,lines",
    "retained_debug_information": [
        "source",
        "lines",
    ],
    "omitted_debug_information": [
        "vars",
    ],
    "reason": (
        "exclude nondeterministic compiler-generated LocalVariableTable names "
        "while retaining source and line diagnostics"
    ),
}
EXPECTED_OFFLINE_DIGEST_COMMAND = (
    '            sha256sum "${output}" | cut -d\' \' -f1 \\\n'
    '              > "${candidate}/offline-output-${suffix}.sha256"'
)
ALLOWED_GLOBAL_SETTINGS_CONTAINERS = frozenset(
    {
        "mirrors",
        "pluginGroups",
        "profiles",
        "proxies",
        "servers",
    }
)
DEFAULT_HTTP_BLOCKER = (
    ("id", "maven-default-http-blocker"),
    ("mirrorOf", "external:http:*"),
    (
        "name",
        "Pseudo repository to mirror external repositories initially using HTTP.",
    ),
    ("url", "http://0.0.0.0/"),
    ("blocked", "true"),
)
EXPECTED_ACTIONS = {
    "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10": 2,
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02": 4,
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c": 1,
    "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25": 4,
    "sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6": 1,
}
EXPECTED_STEPS = {
    "validate": [
        "Check out the reviewed Trino dependency policy",
        "Validate the publication-pending contract",
        "Check the Trino dependency publication lifecycle",
        "Verify the native arm64 builder substrate",
        "Fetch and verify the exact provisionally authorized source",
        "Apply the bounded Web UI security overlay",
        "Fetch and prepare the exact Parquet Jackson remediation sources",
        "Validate the bounded Web UI overlay before merge",
        "Resolve and package the first closed Maven repository",
        "Independently reconstruct the closed Maven repository",
        "Prove two fresh network-none offline source builds",
        "Generate the raw rootfs Maven JAR inventory",
        "Generate the closure-complete Maven CycloneDX SBOM",
        "Scan the dependency closure and record High or Critical findings",
        "Verify and block the complete Maven JAR scan inventory",
        "Retain failed Maven vulnerability diagnostics",
        "Stage the exact Bun lockfiles for dependency analysis",
        "Generate a CycloneDX Bun dependency SBOM",
        "Retain the raw Bun High or Critical findings",
        "Apply OpenVEX and retain adjusted Bun High or Critical findings",
        "Verify and block the raw and OpenVEX-adjusted Bun dependency evidence",
        "Verify complete failed Bun vulnerability diagnostic quartet",
        "Retain failed Bun vulnerability diagnostics",
        "Record the read-only candidate",
        "Retain the read-only-verified candidate",
    ],
    "publish": [
        "Enforce the main-source trust boundary",
        "Check out the reviewed publication policy",
        "Revalidate the write-capable publication boundary",
        "Download the exact read-only-verified candidate",
        "Install checksum-pinned ORAS for publication",
        "Validate the candidate before registry authentication",
        "Publish the immutable run-scoped OCI artifact",
        "Install pinned Cosign after publication",
        "Bind retained SBOM and scan evidence to the published digest",
        "Keyless-sign and attest the exact OCI manifest",
        "Prove anonymous exact-digest retrieval",
        "Record review-pending publication evidence",
        "Retain review-pending publication evidence",
    ],
}
ACTION_RE = re.compile(r"^\s*uses:\s*([^#\s]+)", re.MULTILINE)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAVEN_TRANSFER_EVENT_RE = re.compile(
    r"^\[INFO\]\s+Download(?:ing|ed) from "
    r"(?P<repository>[A-Za-z0-9_.-]+):\s+(?P<url>\S+)"
)
MAVEN_TRANSFER_EVENT_PREFIX_RE = re.compile(
    r"^\[INFO\]\s+Download(?:ing|ed) from "
)
LOWER_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_PROJECT_SELECTION = (
    ":trino-server,:trino-server-core,:trino-server-main,"
    ":trino-hdfs,:trino-iceberg"
)
EXPECTED_RESOLUTION_COMMAND = (
    "mvn --batch-mode --show-version --errors --strict-checksums "
    "--ignore-transitive-repositories "
    "--settings /policy/settings.xml -Dmaven.repo.local=/m2 "
    "-Dproject.build.outputTimestamp=2026-07-18T00:36:39Z "
    "--file /workspace/pom.xml "
    f"-pl '{EXPECTED_PROJECT_SELECTION}' -am "
    "clean install -DskipTests -Dmaven.source.skip=true -Dair.check.skip-all"
)
EXPECTED_SERVER_DISTRIBUTION_ROOT = "trino-server-483"
EXPECTED_SERVER_DISTRIBUTION_EMPTY_DIRECTORIES = frozenset(
    {f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/trino-server-core-483"}
)
EXPECTED_SERVER_DISTRIBUTION_FILES = frozenset(
    {
        f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/bin/launcher",
        (
            f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/lib/"
            "io.trino_trino-server-main-483.jar"
        ),
        (
            f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/lib/"
            "io.trino_trino-web-ui-483.jar"
        ),
        (
            f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/plugin/iceberg/"
            "io.trino_trino-iceberg-483.jar"
        ),
        (
            f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/plugin/iceberg/hdfs/"
            "io.trino_trino-hdfs-483.jar"
        ),
    }
)
EXPECTED_SERVER_DISTRIBUTION_ROOTS = frozenset(
    {"NOTICE", "README.txt", "bin", "lib", "plugin"}
)


class ContractError(ValueError):
    """Raised when the publisher no longer matches its reviewed contract."""


def _fail(code: str, detail: str) -> None:
    raise ContractError(f"{code}: {detail}")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            _fail("JSON", f"duplicate object key: {key}")
        document[key] = value
    return document


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, json.JSONDecodeError) as error:
        _fail("JSON", f"{path}: {error}")
    if not isinstance(value, dict):
        _fail("JSON", f"{path} root must be an object")
    return value


def _matches_exact_json(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        assert isinstance(value, dict)
        return set(value) == set(expected) and all(
            _matches_exact_json(value[key], expected_item)
            for key, expected_item in expected.items()
        )
    if isinstance(expected, list):
        assert isinstance(value, list)
        return len(value) == len(expected) and all(
            _matches_exact_json(value_item, expected_item)
            for value_item, expected_item in zip(value, expected)
        )
    return value == expected


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        _fail("POLICY_FILE", f"{path}: {error}")


def _read_reviewed_regular_file(
    path: Path, *, code: str, max_bytes: int | None = None
) -> bytes:
    try:
        expected = path.lstat()
    except OSError as error:
        _fail(code, f"{path}: {error}")
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_nlink != 1
        or (
            max_bytes is not None
            and not 0 < expected.st_size <= max_bytes
        )
    ):
        _fail(code, f"{path} must be one regular, non-hard-linked file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _fail(code, f"{path}: {error}")
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
            or observed.st_size != expected.st_size
        ):
            _fail(code, f"{path} changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(max_bytes + 1) if max_bytes else source.read()
        if len(payload) != observed.st_size or (
            max_bytes is not None and len(payload) > max_bytes
        ):
            _fail(code, f"{path} changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def _reviewed_regular_identity(path: Path, *, code: str) -> tuple[str, int]:
    try:
        expected = path.lstat()
    except OSError as error:
        _fail(code, f"{path}: {error}")
    if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        _fail(code, f"{path} must be one regular, non-hard-linked file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _fail(code, f"{path}: {error}")
    digest = hashlib.sha256()
    size = 0
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
            or observed.st_size != expected.st_size
        ):
            _fail(code, f"{path} changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        final = os.fstat(descriptor)
        if (
            size != observed.st_size
            or final.st_size != observed.st_size
            or final.st_mtime_ns != observed.st_mtime_ns
        ):
            _fail(code, f"{path} changed while it was read")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def verify_bun_snapshot_identity(descriptor: Path, archive: Path) -> None:
    expected = EXPECTED_BUN_PACKAGE_CACHE["reviewed_snapshot"]
    descriptor_sha256, _ = _reviewed_regular_identity(
        descriptor,
        code="BUN_SNAPSHOT_IDENTITY",
    )
    archive_sha256, archive_size = _reviewed_regular_identity(
        archive,
        code="BUN_SNAPSHOT_IDENTITY",
    )
    if descriptor_sha256 != expected["manifest_sha256"]:
        _fail("BUN_SNAPSHOT_IDENTITY", "manifest SHA-256 differs")
    if (
        archive_sha256 != expected["archive_sha256"]
        or archive_size != expected["archive_size"]
    ):
        _fail("BUN_SNAPSHOT_IDENTITY", "archive identity differs")


def _validate_openvex(root: Path) -> None:
    expected = EXPECTED_SOURCE_OVERLAY["vulnerability_assessment"]["openvex"]
    path = root / expected["path"]
    if _sha256(path) != expected["sha256"]:
        _fail("SOURCE_VEX", "OpenVEX SHA-256 differs")
    document = _load_json(path)
    expected_document = {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": (
            "https://github.com/TommyKammy/Shirokuma/issues/63"
            "#trino-483-react-router-ghsa-qwww-vcr4-c8h2"
        ),
        "author": "TommyKammy/Shirokuma",
        "timestamp": "2026-07-26T03:42:59Z",
        "version": 1,
        "statements": [
            {
                "vulnerability": {"@id": "GHSA-qwww-vcr4-c8h2"},
                "products": [{"@id": "pkg:npm/react-router@7.18.1"}],
                "status": "not_affected",
                "justification": "vulnerable_code_not_in_execute_path",
                "impact_statement": (
                    "The advisory affects unstable React Server Components APIs. "
                    "Trino 483 imports only client-side HashRouter, Routes, Route, "
                    "Navigate, Link, location, parameter, and search-parameter APIs; "
                    "it does not import or invoke unstable RSC APIs."
                ),
            }
        ],
    }
    if not _matches_exact_json(document, expected_document):
        _fail("SOURCE_VEX", "OpenVEX document differs")


def _validate_source_overlay_contract(
    root: Path,
    contract: Mapping[str, Any],
    *,
    at: dt.datetime | None,
) -> None:
    overlay = contract.get("source", {}).get("source_overlay")
    if not _matches_exact_json(overlay, EXPECTED_SOURCE_OVERLAY):
        _fail("SOURCE_OVERLAY", "bounded Web UI overlay contract differs")
    patch = EXPECTED_SOURCE_OVERLAY["patch"]
    if _sha256(root / patch["path"]) != patch["sha256"]:
        _fail("SOURCE_OVERLAY", "source overlay SHA-256 differs")
    _validate_openvex(root)
    expires = _parse_time(EXPECTED_SOURCE_OVERLAY["expires_at"])
    authorization_expires = _parse_time(contract["authorization"]["expires_at"])
    if expires > authorization_expires:
        _fail("SOURCE_OVERLAY", "overlay outlives source authorization")
    if at is not None and at >= expires:
        _fail("SOURCE_OVERLAY_EXPIRED", expires.isoformat())


def _validate_source_remediation_contract(
    contract: Mapping[str, Any],
    *,
    at: dt.datetime | None,
) -> None:
    remediation = contract.get("source_remediation")
    if not _matches_exact_json(remediation, EXPECTED_SOURCE_REMEDIATION):
        _fail("SOURCE_REMEDIATION", "exact Parquet source remediation differs")
    expires = _parse_time(EXPECTED_SOURCE_REMEDIATION["expires_at"])
    authorization_expires = _parse_time(contract["authorization"]["expires_at"])
    if expires > authorization_expires:
        _fail(
            "SOURCE_REMEDIATION",
            "Parquet remediation outlives source authorization",
        )
    if at is not None and at >= expires:
        _fail("SOURCE_REMEDIATION_EXPIRED", expires.isoformat())


def _validate_distribution_remediation_contract(
    root: Path,
    contract: Mapping[str, Any],
    *,
    at: dt.datetime | None,
) -> None:
    remediation = contract.get("source", {}).get(
        "distribution_remediation"
    )
    if not _matches_exact_json(
        remediation,
        EXPECTED_DISTRIBUTION_REMEDIATION,
    ):
        _fail(
            "DISTRIBUTION_REMEDIATION",
            "exact Iceberg-only Maven closure remediation differs",
        )
    patch = EXPECTED_DISTRIBUTION_REMEDIATION["patch"]
    patch_path = root / patch["path"]
    if _sha256(patch_path) != patch["sha256"]:
        _fail("DISTRIBUTION_REMEDIATION", "source patch SHA-256 differs")
    _validate_zero_context_patch(
        patch_path,
        set(EXPECTED_DISTRIBUTION_REMEDIATION["permitted_paths"]),
    )
    expires = _parse_time(EXPECTED_DISTRIBUTION_REMEDIATION["expires_at"])
    authorization_expires = _parse_time(contract["authorization"]["expires_at"])
    if expires > authorization_expires:
        _fail(
            "DISTRIBUTION_REMEDIATION",
            "distribution remediation outlives source authorization",
        )
    if at is not None and at >= expires:
        _fail("DISTRIBUTION_REMEDIATION_EXPIRED", expires.isoformat())


def _validate_build_plugin_remediation_contract(
    root: Path,
    contract: Mapping[str, Any],
    *,
    at: dt.datetime | None,
) -> None:
    remediation = contract.get("source", {}).get("build_plugin_remediation")
    if not _matches_exact_json(remediation, EXPECTED_BUILD_PLUGIN_REMEDIATION):
        _fail(
            "BUILD_PLUGIN_REMEDIATION",
            "exact Maven build-plugin remediation differs",
        )
    patch = EXPECTED_BUILD_PLUGIN_REMEDIATION["patch"]
    patch_path = root / patch["path"]
    if _sha256(patch_path) != patch["sha256"]:
        _fail("BUILD_PLUGIN_REMEDIATION", "source patch SHA-256 differs")
    _validate_zero_context_patch(
        patch_path,
        set(EXPECTED_BUILD_PLUGIN_REMEDIATION["permitted_paths"]),
    )
    expires = _parse_time(EXPECTED_BUILD_PLUGIN_REMEDIATION["expires_at"])
    authorization_expires = _parse_time(contract["authorization"]["expires_at"])
    if expires > authorization_expires:
        _fail(
            "BUILD_PLUGIN_REMEDIATION",
            "build-plugin remediation outlives source authorization",
        )
    if at is not None and at >= expires:
        _fail("BUILD_PLUGIN_REMEDIATION_EXPIRED", expires.isoformat())

    evidence = EXPECTED_BUILD_PLUGIN_REMEDIATION["feasibility_evidence"]
    validation = _load_json(root / AUTHORIZED_FEASIBILITY_RECORD_PATH)
    if not _matches_exact_json(
        {
            "boundary": validation.get("boundary"),
            "offline_inputs": validation.get("offline_inputs"),
            "result": validation.get("result"),
            "subject": validation.get("subject"),
        },
        {
            "boundary": {
                "candidate_patch_sha256": patch["sha256"],
                "candidate_postimage_sha256": (
                    EXPECTED_BUILD_PLUGIN_REMEDIATION["postimages"]["pom.xml"]
                ),
                "dependency_artifact_produced": False,
                "image_or_runtime_change_permitted": False,
                "publication_permitted": False,
                "source_remediation_activated": False,
                "state": "preauthorization_feasibility_only",
            },
            "offline_inputs": validation.get("offline_inputs"),
            "result": {
                "authorization_use_permitted": False,
                "fresh_closure_sbom_and_scan_not_run": True,
                "full_clean_install_not_run": True,
                "owner_decision_still_required": True,
                "status": "passed",
            },
            "subject": {
                "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
                "reviewed_commit": evidence["reviewed_commit"],
                "run_attempt": 1,
                "run_id": 31072144404,
                "workflow": "Trino 483 Maven remediation feasibility",
                "workflow_execution_commit": evidence[
                    "workflow_execution_commit"
                ],
                "workflow_ref": (
                    "TommyKammy/Shirokuma/.github/workflows/"
                    "trino-maven-remediation-feasibility.yml@refs/pull/142/merge"
                ),
                "workflow_run": evidence["workflow_run"],
            },
        },
    ):
        _fail(
            "BUILD_PLUGIN_REMEDIATION_EVIDENCE",
            "retained validation record differs",
        )
    offline_inputs = validation.get("offline_inputs", {})
    if (
        offline_inputs.get("archive", {}).get("sha256")
        != evidence["archive_sha256"]
        or offline_inputs.get("archive", {}).get("bytes")
        != evidence["archive_bytes"]
        or offline_inputs.get("manifest", {}).get("sha256")
        != evidence["manifest_sha256"]
        or offline_inputs.get("file_count")
        != evidence["manifest_file_count"]
        or offline_inputs.get("reproducible_inputs_retained") is not True
    ):
        _fail(
            "BUILD_PLUGIN_REMEDIATION_EVIDENCE",
            "retained archive identity differs",
        )
    receipt = _load_json(root / AUTHORIZED_FEASIBILITY_RECEIPT_PATH)
    if (
        receipt.get("artifact", {}).get("id") != evidence["artifact_id"]
        or receipt.get("artifact", {}).get("digest")
        != evidence["artifact_digest"]
        or receipt.get("artifact", {}).get("expires_at")
        != evidence["artifact_expires_at"]
        or receipt.get("boundary", {}).get("authorization_use_permitted")
        is not True
        or receipt.get("boundary", {}).get("owner_decision_record")
        != EXPECTED_BUILD_PLUGIN_REMEDIATION["approval_record"]
        or receipt.get("independent_reaudit", {}).get("result")
        != evidence["independent_reaudit"]
        or receipt.get("validation_record", {}).get("path")
        != AUTHORIZED_FEASIBILITY_RECORD_PATH.as_posix()
        or receipt.get("workflow_run") != evidence["workflow_run"]
    ):
        _fail(
            "BUILD_PLUGIN_REMEDIATION_EVIDENCE",
            "independent artifact receipt differs",
        )


def _validate_zero_context_patch(
    path: Path,
    permitted_paths: set[str],
) -> None:
    payload = _read_reviewed_regular_file(
        path,
        code="DISTRIBUTION_REMEDIATION_PATCH",
    )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        _fail("DISTRIBUTION_REMEDIATION_PATCH", str(error))
    lines = text.splitlines()
    if (
        not payload.endswith(b"\n")
        or b"\r" in payload
        or any(line.endswith((" ", "\t")) for line in lines)
        or any(line.startswith(" ") for line in lines)
        or any(
            marker in text
            for marker in (
                "[full diff:",
                "Changes:",
                "... (more changes truncated)",
            )
        )
    ):
        _fail(
            "DISTRIBUTION_REMEDIATION_PATCH",
            "patch must be a canonical zero-context unified diff",
        )
    headers: list[tuple[str, str]] = []
    for line in lines:
        match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
        if match:
            headers.append((match.group(1), match.group(2)))
        if line.startswith("@@") and not re.fullmatch(
            r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@",
            line,
        ):
            _fail(
                "DISTRIBUTION_REMEDIATION_PATCH",
                f"noncanonical hunk header: {line}",
            )
    observed_paths = [left for left, right in headers if left == right]
    if (
        len(headers) != len(permitted_paths)
        or len(observed_paths) != len(headers)
        or set(observed_paths) != permitted_paths
    ):
        _fail(
            "DISTRIBUTION_REMEDIATION_PATCH",
            f"patch paths differ: {headers!r}",
        )


def _validate_blocker_evidence(
    root: Path,
    *,
    at: dt.datetime | None = None,
) -> None:
    record = _load_json(root / BLOCKER_CLASSIFICATION_PATH)
    if (
        not _matches_exact_json(record.get("schema_version"), 1)
        or record.get("record_path") != BLOCKER_CLASSIFICATION_PATH.as_posix()
        or not _matches_exact_json(
            record.get("subject"),
            EXPECTED_BLOCKER_SUBJECT,
        )
        or not _matches_exact_json(
            record.get("summary"),
            EXPECTED_BLOCKER_SUMMARY,
        )
    ):
        _fail("BLOCKER_EVIDENCE", "classification identity or summary differs")
    if (
        not _matches_exact_json(
            record.get("classification"),
            EXPECTED_BLOCKER_CLASSIFICATION,
        )
        or not _matches_exact_json(
            record.get("next_action"),
            EXPECTED_BLOCKER_NEXT_ACTION,
        )
    ):
        _fail("BLOCKER_EVIDENCE", "owner-facing policy boundary differs")

    inputs = record.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(EXPECTED_BLOCKER_INPUTS):
        _fail("BLOCKER_EVIDENCE", "retained input inventory differs")
    loaded_inputs: dict[str, dict[str, Any]] = {}
    for name, expected in EXPECTED_BLOCKER_INPUTS.items():
        observed = inputs.get(name)
        if not isinstance(observed, dict) or any(
            not _matches_exact_json(observed.get(field), value)
            for field, value in expected.items()
        ):
            _fail("BLOCKER_EVIDENCE", f"{name} record differs")
        if name in {"run_scoped_maven_manifest", "raw_rootfs_sbom"} and (
            observed.get("retained_in_actions_artifact") is not True
            or observed.get("retained_in_repository") is not True
        ):
            _fail("BLOCKER_EVIDENCE", f"{name} retention differs")
        evidence_path = root / expected["path"]
        payload = _read_reviewed_regular_file(
            evidence_path,
            code="BLOCKER_EVIDENCE",
        )
        if (
            len(payload) != expected["bytes"]
            or hashlib.sha256(payload).hexdigest() != expected["sha256"]
        ):
            _fail("BLOCKER_EVIDENCE", f"{name} bytes or hash differ")
        loaded_inputs[name] = _load_json(evidence_path)

    report_record = inputs["raw_trivy_report"]
    report = loaded_inputs["raw_trivy_report"]
    if (
        report.get("ReportID") != report_record.get("report_id")
        or report.get("CreatedAt") != report_record.get("created_at")
        or report.get("Trivy", {}).get("Version")
        != report_record.get("trivy_version")
    ):
        _fail("BLOCKER_EVIDENCE", "Trivy report metadata differs")
    for name in ("closure_complete_sbom", "raw_rootfs_sbom"):
        if loaded_inputs[name].get("bomFormat") != "CycloneDX":
            _fail("BLOCKER_EVIDENCE", f"{name} is not CycloneDX")

    manifest_path = (
        root / EXPECTED_BLOCKER_INPUTS["run_scoped_maven_manifest"]["path"]
    )
    closure_path = (
        root / EXPECTED_BLOCKER_INPUTS["closure_complete_sbom"]["path"]
    )
    rootfs_path = root / EXPECTED_BLOCKER_INPUTS["raw_rootfs_sbom"]["path"]
    report_path = root / EXPECTED_BLOCKER_INPUTS["raw_trivy_report"]["path"]
    verify_maven_scan(
        manifest_path,
        closure_path,
        report_path,
        allow_high_critical=True,
    )

    def components_by_ref(
        name: str,
        path: Path,
    ) -> dict[str, dict[str, Any]]:
        components = _cyclonedx_components(loaded_inputs[name], path)
        observed = {
            component.get("bom-ref"): component
            for component in components
            if isinstance(component.get("bom-ref"), str)
            and component["bom-ref"]
        }
        if len(observed) != len(components):
            _fail("BLOCKER_EVIDENCE", f"{name} bom-ref closure differs")
        return observed

    rootfs_components = components_by_ref("raw_rootfs_sbom", rootfs_path)
    closure_components = components_by_ref(
        "closure_complete_sbom",
        closure_path,
    )
    mismatched_rootfs_refs = sorted(
        reference
        for reference, component in rootfs_components.items()
        if closure_components.get(reference) != component
    )
    if mismatched_rootfs_refs:
        _fail(
            "BLOCKER_EVIDENCE",
            "closure SBOM does not retain the exact rootfs component set: "
            f"{mismatched_rootfs_refs!r}",
        )

    vulnerabilities: list[dict[str, Any]] = []
    results = report.get("Results")
    if not isinstance(results, list):
        _fail("BLOCKER_EVIDENCE", "Trivy Results must be a list")
    for result in results:
        if not isinstance(result, dict):
            _fail("BLOCKER_EVIDENCE", "Trivy result must be an object")
        findings = result.get("Vulnerabilities") or []
        if not isinstance(findings, list) or not all(
            isinstance(finding, dict) for finding in findings
        ):
            _fail("BLOCKER_EVIDENCE", "Trivy vulnerabilities differ")
        vulnerabilities.extend(findings)

    high = [v for v in vulnerabilities if v.get("Severity") == "HIGH"]
    critical = [
        v for v in vulnerabilities if v.get("Severity") == "CRITICAL"
    ]
    recomputed = {
        "high_occurrences": len(high),
        "critical_occurrences": len(critical),
        "vulnerability_ids": len(
            {v.get("VulnerabilityID") for v in vulnerabilities}
        ),
        "package_version_groups": len(
            {
                (v.get("PkgName"), v.get("InstalledVersion"))
                for v in vulnerabilities
            }
        ),
        "physical_jar_paths": len(
            {v.get("PkgPath") for v in vulnerabilities}
        ),
        "publication_reached": False,
        "dependency_artifact_produced": False,
        "admission_reached": False,
        "runtime_state_changed": False,
    }
    if recomputed != EXPECTED_BLOCKER_SUMMARY:
        _fail("BLOCKER_EVIDENCE", f"recomputed summary differs: {recomputed!r}")

    def normalized_fixed_versions(value: Any) -> tuple[str, ...]:
        if isinstance(value, list):
            versions = value
        elif isinstance(value, str):
            versions = value.split(",")
        else:
            versions = []
        return tuple(sorted(version.strip() for version in versions if version.strip()))

    observed_findings = sorted(
        (
            finding.get("VulnerabilityID"),
            finding.get("Severity"),
            finding.get("PkgIdentifier", {}).get("PURL"),
            finding.get("InstalledVersion"),
            normalized_fixed_versions(finding.get("FixedVersion")),
            finding.get("PkgPath"),
        )
        for finding in vulnerabilities
    )
    classified = record.get("findings")
    if not isinstance(classified, list) or not all(
        isinstance(finding, dict) for finding in classified
    ):
        _fail("BLOCKER_EVIDENCE", "classified findings must be objects")
    classified_findings = sorted(
        (
            finding.get("vulnerability_id"),
            finding.get("severity"),
            finding.get("purl"),
            finding.get("installed_version"),
            normalized_fixed_versions(
                finding.get(
                    "fixed_versions",
                    finding.get("fixed_version"),
                )
            ),
            finding.get("physical_path"),
        )
        for finding in classified
    )
    if classified_findings != observed_findings:
        _fail("BLOCKER_EVIDENCE", "classified findings differ from Trivy report")
    finding_policy_fields = tuple(EXPECTED_BLOCKER_FINDING_POLICY[0])
    observed_finding_policy = [
        {field: finding.get(field) for field in finding_policy_fields}
        for finding in classified
    ]
    if not _matches_exact_json(
        observed_finding_policy,
        EXPECTED_BLOCKER_FINDING_POLICY,
    ):
        _fail("BLOCKER_EVIDENCE", "finding policy classification differs")

    feasibility = record.get("focused_feasibility")
    expected_feasibility = {
        "status": "candidate_only_not_authorized_not_active",
        "baseline": EXPECTED_BLOCKER_BASELINE,
        "candidate": EXPECTED_BLOCKER_CANDIDATE,
        "validation": EXPECTED_BLOCKER_FEASIBILITY_VALIDATION,
    }
    if not _matches_exact_json(feasibility, expected_feasibility):
        _fail("BLOCKER_EVIDENCE", "feasibility boundary differs")
    expires = _parse_time(
        EXPECTED_BLOCKER_FEASIBILITY_VALIDATION["artifact_expires_at"]
    )
    if at is not None and at >= expires:
        _fail(
            "BLOCKER_FEASIBILITY_EXPIRED",
            f"{at.isoformat()} is at or after {expires.isoformat()}",
        )
    retained_feasibility: dict[Path, dict[str, Any]] = {}
    for path, expected in EXPECTED_BLOCKER_FEASIBILITY_FILES.items():
        payload = _read_reviewed_regular_file(
            root / path,
            code="BLOCKER_EVIDENCE",
        )
        if (
            len(payload) != expected["bytes"]
            or hashlib.sha256(payload).hexdigest() != expected["sha256"]
        ):
            _fail("BLOCKER_EVIDENCE", f"feasibility evidence differs: {path}")
        retained_feasibility[path] = _load_json(root / path)
    receipt = retained_feasibility[BLOCKER_FEASIBILITY_RECEIPT_PATH]
    validation = retained_feasibility[BLOCKER_FEASIBILITY_RECORD_PATH]
    if (
        not _matches_exact_json(
            receipt.get("validation_record"),
            {
                "bytes": EXPECTED_BLOCKER_FEASIBILITY_FILES[
                    BLOCKER_FEASIBILITY_RECORD_PATH
                ]["bytes"],
                "path": BLOCKER_FEASIBILITY_RECORD_PATH.as_posix(),
                "sha256": EXPECTED_BLOCKER_FEASIBILITY_FILES[
                    BLOCKER_FEASIBILITY_RECORD_PATH
                ]["sha256"],
            },
        )
        or not _matches_exact_json(
            receipt.get("boundary"),
            {
                "authorization_use_permitted": False,
                "owner_decision_still_required": True,
                "publication_permitted": False,
                "source_remediation_activated": False,
            },
        )
        or not _matches_exact_json(
            receipt.get("independent_reaudit"),
            EXPECTED_FEASIBILITY_REAUDIT,
        )
        or _sha256(root / FEASIBILITY_RETAINED_VERIFIER_PATH)
        != EXPECTED_FEASIBILITY_REAUDIT["verifier"]["sha256"]
        or validation.get("result", {}).get("authorization_use_permitted")
        is not False
        or validation.get("result", {}).get("owner_decision_still_required")
        is not True
        or validation.get("boundary", {}).get("source_remediation_activated")
        is not False
        or validation.get("boundary", {}).get("publication_permitted") is not False
    ):
        _fail("BLOCKER_EVIDENCE", "retained feasibility receipt differs")
    candidate_path = root / EXPECTED_BLOCKER_CANDIDATE["patch_path"]
    candidate_payload = _read_reviewed_regular_file(
        candidate_path,
        code="BLOCKER_EVIDENCE",
    )
    if (
        len(candidate_payload) != EXPECTED_BLOCKER_CANDIDATE["patch_bytes"]
        or hashlib.sha256(candidate_payload).hexdigest()
        != EXPECTED_BLOCKER_CANDIDATE["patch_sha256"]
    ):
        _fail("BLOCKER_EVIDENCE", "candidate patch bytes or hash differ")
    _validate_zero_context_patch(candidate_path, {"pom.xml"})

    baseline_payload = _read_reviewed_regular_file(
        root / BLOCKER_BASELINE_PATH,
        code="BLOCKER_EVIDENCE",
    )
    if (
        len(baseline_payload) != EXPECTED_BLOCKER_BASELINE["retained_bytes"]
        or hashlib.sha256(baseline_payload).hexdigest()
        != EXPECTED_BLOCKER_BASELINE["retained_sha256"]
    ):
        _fail("BLOCKER_EVIDENCE", "candidate baseline bytes or hash differ")
    try:
        baseline = gzip.decompress(baseline_payload)
    except (OSError, EOFError) as error:
        _fail("BLOCKER_EVIDENCE", f"candidate baseline gzip differs: {error}")
    if (
        hashlib.sha256(baseline).hexdigest()
        != EXPECTED_BLOCKER_BASELINE["post_adr_0027_sha256"]
    ):
        _fail("BLOCKER_EVIDENCE", "candidate baseline preimage differs")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "pom.xml").write_bytes(baseline)
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            )
            apply_command = [
                "git",
                "apply",
                "--unidiff-zero",
                "--whitespace=error-all",
                str(candidate_path.resolve()),
            ]
            subprocess.run(
                [*apply_command[:2], "--check", *apply_command[2:]],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                apply_command,
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            )
            postimage = _read_reviewed_regular_file(
                checkout / "pom.xml",
                code="BLOCKER_EVIDENCE",
            )
    except (OSError, subprocess.CalledProcessError) as error:
        _fail("BLOCKER_EVIDENCE", f"candidate patch application failed: {error}")
    if (
        hashlib.sha256(postimage).hexdigest()
        != EXPECTED_BLOCKER_CANDIDATE["postimage_sha256"]
    ):
        _fail("BLOCKER_EVIDENCE", "candidate patch postimage differs")


def _validate_react_router_import_inventory(checkout: Path) -> None:
    source_root = (
        checkout
        / "core/trino-web-ui/src/main/resources/webapp/src"
    )
    observed: list[dict[str, str]] = []
    forbidden_markers = (
        "unstable_RSC",
        "unstable_createCallServer",
        "server.rsc",
        "react-server",
    )
    try:
        paths = sorted(
            path
            for path in source_root.rglob("*")
            if path.is_file() and path.suffix in {".js", ".jsx", ".ts", ".tsx"}
        )
    except OSError as error:
        _fail("SOURCE_IMPORT_INVENTORY", str(error))
    for path in paths:
        payload = _read_reviewed_regular_file(
            path,
            code="SOURCE_IMPORT_INVENTORY",
        )
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            _fail("SOURCE_IMPORT_INVENTORY", f"{path}: {error}")
        if any(marker in text for marker in forbidden_markers):
            _fail("SOURCE_IMPORT_INVENTORY", f"RSC marker in {path}")
        for line in text.splitlines():
            if "react-router" not in line:
                continue
            statement = line.strip()
            if re.fullmatch(
                r"import .+ from ['\"]react-router(?:-dom)?['\"]",
                statement,
            ) is None:
                _fail(
                    "SOURCE_IMPORT_INVENTORY",
                    f"non-static React Router reference in {path}: {statement}",
                )
            observed.append(
                {
                    "path": path.relative_to(checkout).as_posix(),
                    "statement": statement,
                }
            )
    expected = EXPECTED_SOURCE_OVERLAY["react_router_import_inventory"]
    observed_inventory = sorted(
        observed,
        key=lambda item: (item["path"], item["statement"]),
    )
    expected_inventory = sorted(
        expected,
        key=lambda item: (item["path"], item["statement"]),
    )
    if observed_inventory != expected_inventory:
        _fail("SOURCE_IMPORT_INVENTORY", f"imports differ: {observed!r}")


def apply_source_overlay(root: Path, checkout: Path) -> None:
    audit_source(root, checkout)
    contract = _load_json(root / CONTRACT_PATH)
    now = dt.datetime.now(dt.timezone.utc)
    _validate_authorization(contract, at=now)
    _validate_source_overlay_contract(root, contract, at=now)
    _validate_distribution_remediation_contract(root, contract, at=now)
    _validate_build_plugin_remediation_contract(root, contract, at=now)
    overlay = EXPECTED_SOURCE_OVERLAY
    distribution = EXPECTED_DISTRIBUTION_REMEDIATION
    build_plugin = EXPECTED_BUILD_PLUGIN_REMEDIATION
    boundaries = (overlay, distribution, build_plugin)
    permitted: set[str] = set()
    for boundary in boundaries:
        boundary_permitted = set(boundary["permitted_paths"])
        if boundary_permitted != set(
            boundary["preimages"]
        ) or boundary_permitted != set(boundary["postimages"]):
            _fail("SOURCE_OVERLAY", "preimage/postimage path sets differ")
        permitted |= boundary_permitted
    _validate_react_router_import_inventory(checkout)
    try:
        for boundary in boundaries:
            _apply_source_overlay_boundary(root, checkout, boundary)
        changed = set(
            subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "--diff-filter=ACDMRTUXB",
                    "HEAD",
                    "--",
                ],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        _fail("SOURCE_OVERLAY_APPLY", str(error))
    if changed != permitted or untracked:
        _fail(
            "SOURCE_OVERLAY_APPLY",
            f"changed={sorted(changed)!r}, untracked={untracked!r}",
        )
    _validate_react_router_import_inventory(checkout)


def _apply_source_overlay_boundary(
    root: Path,
    checkout: Path,
    boundary: Mapping[str, Any],
) -> None:
    for relative, expected in boundary["preimages"].items():
        payload = _read_reviewed_regular_file(
            checkout / relative,
            code="SOURCE_OVERLAY_PREIMAGE",
        )
        if hashlib.sha256(payload).hexdigest() != expected:
            _fail("SOURCE_OVERLAY_PREIMAGE", relative)
    patch = root / boundary["patch"]["path"]
    command = [
        "git",
        "apply",
        *boundary["apply_arguments"],
        str(patch),
    ]
    subprocess.run(
        [*command[:2], "--check", *command[2:]],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        command,
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    for relative, expected in boundary["postimages"].items():
        payload = _read_reviewed_regular_file(
            checkout / relative,
            code="SOURCE_OVERLAY_POSTIMAGE",
        )
        if hashlib.sha256(payload).hexdigest() != expected:
            _fail("SOURCE_OVERLAY_POSTIMAGE", relative)


def stage_bun_scan_input(checkout: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        _fail("BUN_SCAN_INPUT", f"output already exists: {output}")
    try:
        output.mkdir(mode=0o700)
    except OSError as error:
        _fail("BUN_SCAN_INPUT", f"{output}: {error}")
    for record in EXPECTED_BUN_PACKAGE_CACHE["frozen_lockfiles"]:
        relative = Path(record["path"])
        payload = _read_reviewed_regular_file(
            checkout / relative,
            code="BUN_SCAN_INPUT",
        )
        if hashlib.sha256(payload).hexdigest() != record["sha256"]:
            _fail("BUN_SCAN_INPUT", f"lockfile hash differs: {relative}")
        target = output / relative
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(payload)
            target.chmod(0o444)
        except OSError as error:
            _fail("BUN_SCAN_INPUT", f"{target}: {error}")


def _bun_scan_target(target: object) -> str:
    if not isinstance(target, str) or not target:
        _fail("BUN_SCAN_REPORT", "result target must be a non-empty string")
    normalized = target.replace("\\", "/").removeprefix("./")
    if normalized not in EXPECTED_BUN_SCAN_RESULTS:
        _fail("BUN_SCAN_REPORT", f"unexpected result target: {target}")
    return normalized


def _bun_scan_report(
    report_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    report = _load_json(report_path)
    if (
        not _matches_exact_json(report.get("SchemaVersion"), 2)
        or report.get("ArtifactType") != "filesystem"
    ):
        _fail("BUN_SCAN_REPORT", "unexpected Trivy report envelope")
    results = report.get("Results")
    if not isinstance(results, list):
        _fail("BUN_SCAN_REPORT", "Results must be a list")
    inventories: dict[str, list[dict[str, Any]]] = {}
    findings: list[tuple[str, dict[str, Any]]] = []
    for result in results:
        if not isinstance(result, dict):
            _fail("BUN_SCAN_REPORT", "each result must be an object")
        target = _bun_scan_target(result.get("Target"))
        if target in inventories:
            _fail("BUN_SCAN_REPORT", f"duplicate result target: {target}")
        if result.get("Class") != "lang-pkgs" or result.get("Type") != "bun":
            _fail("BUN_SCAN_REPORT", f"unexpected package type for {target}")
        packages = result.get("Packages")
        if not isinstance(packages, list) or not packages:
            _fail("BUN_SCAN_REPORT", f"no packages detected for {target}")
        for package in packages:
            if (
                not isinstance(package, dict)
                or not isinstance(package.get("Name"), str)
                or not package["Name"]
            ):
                _fail("BUN_SCAN_REPORT", f"malformed package for {target}")
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            _fail("BUN_SCAN_REPORT", f"malformed vulnerabilities for {target}")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                _fail("BUN_SCAN_REPORT", f"malformed finding for {target}")
            findings.append((target, vulnerability))
        inventories[target] = packages
    expected_files = set(EXPECTED_BUN_SCAN_RESULTS)
    if set(inventories) != expected_files:
        _fail(
            "BUN_SCAN_REPORT",
            f"result targets differ: {sorted(inventories)!r}",
        )
    for target, expectation in EXPECTED_BUN_SCAN_RESULTS.items():
        packages = inventories[target]
        names = {package["Name"] for package in packages}
        if len(packages) != expectation["package_count"]:
            _fail(
                "BUN_SCAN_REPORT",
                f"package count differs for {target}: {len(packages)}",
            )
        missing = expectation["required_packages"] - names
        if missing:
            _fail(
                "BUN_SCAN_REPORT",
                f"required packages missing for {target}: {sorted(missing)!r}",
            )
    return inventories, findings


def _verify_raw_bun_finding(findings: list[tuple[str, dict[str, Any]]]) -> None:
    expected = EXPECTED_SOURCE_OVERLAY["vulnerability_assessment"]["raw_finding"]
    if len(findings) != 1:
        _fail(
            "BUN_SCAN_RAW_FINDING",
            f"expected exactly one reviewed finding, found {len(findings)}",
        )
    target, finding = findings[0]
    identifier = finding.get("PkgIdentifier")
    purl = identifier.get("PURL") if isinstance(identifier, dict) else None
    observed = {
        "target": target,
        "vulnerability_id": finding.get("VulnerabilityID"),
        "package": finding.get("PkgName"),
        "installed_version": finding.get("InstalledVersion"),
        "fixed_version": finding.get("FixedVersion"),
        "severity": finding.get("Severity"),
        "purl": purl,
    }
    if observed != expected:
        _fail("BUN_SCAN_RAW_FINDING", f"finding differs: {observed!r}")


def _canonical_inventory(
    inventories: Mapping[str, list[dict[str, Any]]],
) -> dict[str, list[str]]:
    return {
        target: sorted(
            json.dumps(package, sort_keys=True, separators=(",", ":"))
            for package in packages
        )
        for target, packages in inventories.items()
    }


def verify_bun_scan(
    root: Path,
    scan_input: Path,
    raw_report_path: Path,
    adjusted_report_path: Path,
) -> None:
    try:
        root_metadata = scan_input.lstat()
    except OSError as error:
        _fail("BUN_SCAN_INPUT", f"{scan_input}: {error}")
    if not stat.S_ISDIR(root_metadata.st_mode):
        _fail("BUN_SCAN_INPUT", "scan input must be one real directory")
    observed_files: set[str] = set()
    try:
        paths = tuple(scan_input.rglob("*"))
    except OSError as error:
        _fail("BUN_SCAN_INPUT", f"{scan_input}: {error}")
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError as error:
            _fail("BUN_SCAN_INPUT", f"{path}: {error}")
        if stat.S_ISLNK(metadata.st_mode):
            _fail("BUN_SCAN_INPUT", f"symlink is forbidden: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            _fail("BUN_SCAN_INPUT", f"unsafe scan input: {path}")
        observed_files.add(path.relative_to(scan_input).as_posix())
    expected_files = set(EXPECTED_BUN_SCAN_RESULTS)
    if observed_files != expected_files:
        _fail(
            "BUN_SCAN_INPUT",
            f"lockfile set differs: {sorted(observed_files)!r}",
        )
    frozen = {
        record["path"]: record["sha256"]
        for record in EXPECTED_BUN_PACKAGE_CACHE["frozen_lockfiles"]
    }
    if set(frozen) != expected_files:
        _fail("BUN_SCAN_INPUT", "frozen lockfile contract differs")
    for relative in sorted(expected_files):
        payload = _read_reviewed_regular_file(
            scan_input / relative,
            code="BUN_SCAN_INPUT",
        )
        if hashlib.sha256(payload).hexdigest() != frozen[relative]:
            _fail("BUN_SCAN_INPUT", f"lockfile hash differs: {relative}")

    contract = _load_json(root / CONTRACT_PATH)
    _validate_source_overlay_contract(
        root,
        contract,
        at=dt.datetime.now(dt.timezone.utc),
    )
    raw_inventory, raw_findings = _bun_scan_report(raw_report_path)
    adjusted_inventory, adjusted_findings = _bun_scan_report(
        adjusted_report_path
    )
    _verify_raw_bun_finding(raw_findings)
    if adjusted_findings:
        _fail(
            "BUN_SCAN_ADJUSTED_FINDING",
            f"blocking findings remain: {adjusted_findings!r}",
        )
    if _canonical_inventory(raw_inventory) != _canonical_inventory(
        adjusted_inventory
    ):
        _fail(
            "BUN_SCAN_INVENTORY",
            "raw and OpenVEX-adjusted package inventories differ",
        )


def _maven_jar_records(
    descriptor_path: Path,
) -> dict[str, Mapping[str, Any]]:
    descriptor = _load_json(descriptor_path)
    files = descriptor.get("files")
    if (
        not _matches_exact_json(descriptor.get("schema_version"), 2)
        or not isinstance(files, list)
        or not _matches_exact_json(descriptor.get("file_count"), len(files))
    ):
        _fail("MAVEN_SCAN_DESCRIPTOR", "closed Maven descriptor differs")
    records: dict[str, Mapping[str, Any]] = {}
    for record in files:
        if not isinstance(record, dict) or set(record) != {
            "mode",
            "path",
            "repository_origin",
            "sha256",
            "size",
        }:
            _fail("MAVEN_SCAN_DESCRIPTOR", "Maven file identity differs")
        path = record.get("path")
        if isinstance(path, str) and path.endswith(".jar"):
            repository_origin = record.get("repository_origin")
            if not isinstance(repository_origin, str):
                origin_is_expected = False
            elif path == EXPECTED_PARQUET_REMEDIATION_JAR_PATH:
                origin_is_expected = (
                    repository_origin
                    == EXPECTED_PARQUET_SOURCE_REMEDIATION["repository"]
                )
            else:
                origin_is_expected = repository_origin in set(
                    EXPECTED_REPOSITORIES.values()
                )
            if (
                path in records
                or Path(path).is_absolute()
                or any(part in {"", ".", ".."} for part in path.split("/"))
            ):
                _fail(
                    "MAVEN_SCAN_DESCRIPTOR",
                    f"unsafe or duplicate Maven JAR path: {path}",
                )
            mode = record.get("mode")
            digest = record.get("sha256")
            if (
                not isinstance(mode, str)
                or re.fullmatch(r"0[0-7]{3}", mode) is None
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or type(record.get("size")) is not int
                or record["size"] <= 0
                or not origin_is_expected
            ):
                _fail(
                    "MAVEN_SCAN_DESCRIPTOR",
                    f"invalid Maven JAR identity: {path}",
                )
            records[path] = record
    if not records:
        _fail("MAVEN_SCAN_DESCRIPTOR", "Maven descriptor contains no JARs")
    return records


def _maven_jar_paths(descriptor_path: Path) -> set[str]:
    return set(_maven_jar_records(descriptor_path))


def _maven_purl(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 4 or not path.endswith(".jar"):
        _fail("MAVEN_SBOM", f"invalid Maven JAR path: {path}")
    group = ".".join(parts[:-3])
    artifact, version, filename = parts[-3:]
    prefix = f"{artifact}-{version}"
    if not filename.startswith(prefix):
        _fail("MAVEN_SBOM", f"JAR does not match Maven coordinates: {path}")
    classifier_suffix = filename[len(prefix) : -4]
    if classifier_suffix and (
        not classifier_suffix.startswith("-")
        or classifier_suffix == "-"
    ):
        _fail("MAVEN_SBOM", f"invalid Maven JAR filename: {path}")
    classifier = classifier_suffix.removeprefix("-")
    qualifier = f"?classifier={quote(classifier, safe='')}" if classifier else ""
    return (
        f"pkg:maven/{quote(group, safe='.')}/{quote(artifact, safe='')}"
        f"@{quote(version, safe='.')}{qualifier}"
    )


def _maven_classifier(path: str) -> str:
    artifact, version, filename = path.split("/")[-3:]
    prefix = f"{artifact}-{version}"
    return filename[len(prefix) : -4].removeprefix("-")


def _maven_rootfs_discovery_purls(path: str) -> set[str]:
    purl = _maven_purl(path)
    if not _maven_classifier(path):
        return {purl}
    return {purl, purl.split("?classifier=", 1)[0]}


def _safe_jar_member_type(entry: zipfile.ZipInfo) -> bool:
    member_type = stat.S_IFMT(entry.external_attr >> 16)
    if entry.is_dir():
        return member_type in {0, stat.S_IFDIR}
    return member_type in {0, stat.S_IFREG}


def _zip_directory_summary(payload: bytes) -> tuple[int, int] | None:
    minimum_eocd_size = 22
    search_start = max(
        0,
        len(payload) - minimum_eocd_size - 65535,
    )
    search_end = len(payload)
    while True:
        eocd_offset = payload.rfind(
            b"PK\x05\x06",
            search_start,
            search_end,
        )
        if eocd_offset < 0:
            return None
        search_end = eocd_offset
        if eocd_offset + minimum_eocd_size > len(payload):
            continue
        eocd = payload[eocd_offset : eocd_offset + minimum_eocd_size]
        disk_number = int.from_bytes(eocd[4:6], "little")
        directory_disk = int.from_bytes(eocd[6:8], "little")
        disk_entries = int.from_bytes(eocd[8:10], "little")
        total_entries = int.from_bytes(eocd[10:12], "little")
        directory_size = int.from_bytes(eocd[12:16], "little")
        directory_offset = int.from_bytes(eocd[16:20], "little")
        comment_size = int.from_bytes(eocd[20:22], "little")
        if (
            disk_number == 0
            and directory_disk == 0
            and disk_entries == total_entries
            and total_entries != 0xFFFF
            and directory_size != 0xFFFFFFFF
            and directory_offset != 0xFFFFFFFF
            and eocd_offset + minimum_eocd_size + comment_size
            == len(payload)
            and directory_offset + directory_size == eocd_offset
        ):
            return total_entries, directory_size


def _jar_entries(payload: bytes, path: str) -> tuple[zipfile.ZipFile, list[str]]:
    directory_summary = _zip_directory_summary(payload)
    if (
        len(payload) > MAX_OMITTED_JAR_ARCHIVE_BYTES
        or directory_summary is None
        or directory_summary[0] > MAX_OMITTED_JAR_MEMBERS
        or directory_summary[1]
        > MAX_OMITTED_JAR_CENTRAL_DIRECTORY_BYTES
    ):
        _fail(
            "MAVEN_SBOM_ROOTFS",
            f"{path} exceeds omitted-JAR archive limits",
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        _fail("MAVEN_SBOM_ROOTFS", f"{path} is not a valid JAR: {error}")
    names = [entry.filename for entry in entries]
    if (
        len(names) > MAX_OMITTED_JAR_MEMBERS
        or len(names) != len(set(names))
        or any(
            entry.flag_bits & 0x1
            or entry.filename.startswith("/")
            or "\\" in entry.filename
            or not _safe_jar_member_type(entry)
            or any(
                part in {"", ".", ".."}
                for part in entry.filename.rstrip("/").split("/")
            )
            for entry in entries
        )
    ):
        archive.close()
        _fail(
            "MAVEN_SBOM_ROOTFS",
            (
                f"{path} contains unsafe, encrypted, duplicate, "
                "or special entries"
            ),
        )
    if any(name.casefold().endswith(".jar") for name in names):
        archive.close()
        _fail(
            "MAVEN_SBOM_ROOTFS",
            f"{path} contains an undiscovered nested JAR",
        )
    expanded_size = 0
    for entry in entries:
        expanded_size += entry.file_size
        if (
            entry.file_size > MAX_OMITTED_JAR_MEMBER_BYTES
            or expanded_size > MAX_OMITTED_JAR_EXPANDED_BYTES
            or (
                entry.file_size > 0
                and (
                    entry.compress_size <= 0
                    or entry.file_size
                    > entry.compress_size * MAX_OMITTED_JAR_COMPRESSION_RATIO
                )
            )
        ):
            archive.close()
            _fail(
                "MAVEN_SBOM_ROOTFS",
                f"{path} exceeds omitted-JAR decompression limits",
            )
    return archive, names


def _archive_contains_nested_zip(archive: zipfile.ZipFile) -> bool:
    return any(
        not entry.is_dir()
        and zipfile.is_zipfile(io.BytesIO(archive.read(entry)))
        for entry in archive.infolist()
    )


def _validate_rootfs_discovery_omissions(
    repository_path: Path,
    records: Mapping[str, Mapping[str, Any]],
    missing_paths: set[str],
    rootfs_components: list[dict[str, Any]],
) -> dict[str, str]:
    omission_contract = {
        entry["path"]: entry
        for entry in EXPECTED_TRIVY_ROOTFS_OMISSIONS
    }
    unknown_paths = sorted(missing_paths - set(omission_contract))
    if unknown_paths:
        _fail(
            "MAVEN_SBOM_ROOTFS",
            (
                "rootfs discovery omitted JARs outside the reviewed "
                f"closed set: {unknown_paths!r}"
            ),
        )
    try:
        repository_root = repository_path.resolve(strict=True)
    except OSError as error:
        _fail("MAVEN_SBOM_ROOTFS", f"{repository_path}: {error}")
    if not repository_root.is_dir():
        _fail("MAVEN_SBOM_ROOTFS", f"{repository_path} is not a directory")
    rootfs_identities = {
        (component["purl"], file_path)
        for component in rootfs_components
        if isinstance(component.get("purl"), str)
        and component["purl"]
        for file_path in _component_file_paths(component)
    }
    manifest_verified_base_purls: set[str] = set()
    discovery: dict[str, str] = {}
    for path in sorted(
        missing_paths,
        key=lambda item: (bool(_maven_classifier(item)), item),
    ):
        record = records[path]
        contract_entry = omission_contract[path]
        purl = _maven_purl(path)
        role = contract_entry["role"]
        classifier = _maven_classifier(path)
        if (
            role
            not in {
                "supplemental-sources",
                "supplemental-tests",
                "base-coordinate",
            }
            or purl != contract_entry["purl"]
            or (
                role == "supplemental-sources"
                and classifier != "sources"
            )
            or (
                role == "supplemental-tests"
                and classifier != "tests"
            )
            or (role == "base-coordinate" and classifier)
        ):
            _fail(
                "MAVEN_SBOM_ROOTFS",
                f"{path} differs from its reviewed omission identity",
            )
        try:
            resolved_candidate = (repository_root / path).resolve(strict=True)
            candidate_stat = (repository_root / path).lstat()
            candidate_mode = stat.S_IMODE(candidate_stat.st_mode)
        except OSError as error:
            _fail("MAVEN_SBOM_ROOTFS", f"{path}: {error}")
        if (
            not resolved_candidate.is_relative_to(repository_root)
            or candidate_mode != int(str(record["mode"]), 8)
            or candidate_stat.st_size > MAX_OMITTED_JAR_ARCHIVE_BYTES
            or record["size"] > MAX_OMITTED_JAR_ARCHIVE_BYTES
        ):
            _fail(
                "MAVEN_SBOM_ROOTFS",
                (
                    f"{path} escapes the repository, has the wrong mode, "
                    "or exceeds omitted-JAR archive limits"
                ),
            )
        payload = _read_reviewed_regular_file(
            repository_root / path,
            code="MAVEN_SBOM_ROOTFS",
        )
        if (
            len(payload) != record["size"]
            or hashlib.sha256(payload).hexdigest() != record["sha256"]
        ):
            _fail(
                "MAVEN_SBOM_ROOTFS",
                f"{path} differs from the closed Maven descriptor",
            )

        base_purl = purl.split("?classifier=", 1)[0]
        artifact, version = path.split("/")[-3:-1]
        base_path = path.rsplit("/", 1)[0] + f"/{artifact}-{version}.jar"
        if (
            classifier
            and (
                base_path not in records
                or (base_purl, base_path) not in rootfs_identities
            )
            and base_purl not in manifest_verified_base_purls
        ):
            _fail(
                "MAVEN_SBOM_ROOTFS",
                (
                    f"{path} has no descriptor-bound top-level "
                    "rootfs or contract-authorized base coordinate"
                ),
            )

        archive, names = _jar_entries(payload, path)
        try:
            if not any(not entry.is_dir() for entry in archive.infolist()):
                _fail("MAVEN_SBOM_ROOTFS", f"{path} is an empty JAR")
            if role == "supplemental-sources" and not any(
                name.endswith(
                    (".java", ".kt", ".kts", ".scala", ".groovy")
                )
                for name in names
            ):
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} contains no source payload",
                )
            if role == "supplemental-tests" and not any(
                name.endswith(".class") for name in names
            ):
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} contains no test class payload",
                )
            try:
                contains_nested_zip = _archive_contains_nested_zip(archive)
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} contains an unreadable member: {error}",
                )
            if contains_nested_zip:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} contains an undiscovered nested archive",
                )
        finally:
            archive.close()

        if role == "base-coordinate":
            manifest_verified_base_purls.add(base_purl)
            discovery[path] = "contract-base-coordinate"
        elif role == "supplemental-sources":
            discovery[path] = "contract-supplemental-sources"
        else:
            discovery[path] = "contract-supplemental-tests"
    return discovery


def generate_maven_sbom(
    descriptor_path: Path,
    repository_path: Path,
    rootfs_sbom_path: Path,
    output_path: Path,
) -> None:
    descriptor = _load_json(descriptor_path)
    files = descriptor.get("files")
    if not isinstance(files, list):
        _fail("MAVEN_SBOM", "closed Maven descriptor files are missing")
    records = _maven_jar_records(descriptor_path)
    expected_paths = set(records)
    rootfs = _load_json(rootfs_sbom_path)
    rootfs_components = _cyclonedx_components(rootfs, rootfs_sbom_path)
    observed_rootfs = _cyclonedx_top_level_jar_paths(
        rootfs,
        rootfs_sbom_path,
    )
    unexpected_rootfs = observed_rootfs - expected_paths
    if unexpected_rootfs:
        _fail(
            "MAVEN_SBOM_ROOTFS",
            (
                "rootfs discovery escapes the closed JAR set: "
                f"unexpected={sorted(unexpected_rootfs)!r}"
            ),
        )
    rootfs_identities = {
        (component.get("purl"), path)
        for component in rootfs_components
        for path in _component_file_paths(component)
        if _is_top_level_jar_path(path)
    }
    discovered_paths = {
        path
        for purl, path in rootfs_identities
        if path in expected_paths
        and purl in _maven_rootfs_discovery_purls(path)
    }
    classifier_erased_discovery = {
        path
        for purl, path in rootfs_identities
        if path in expected_paths
        and _maven_classifier(path)
        and purl == _maven_purl(path).split("?classifier=", 1)[0]
    }
    discovery_omissions = _validate_rootfs_discovery_omissions(
        repository_path,
        records,
        expected_paths - discovered_paths,
        rootfs_components,
    )
    components = copy.deepcopy(rootfs_components)
    component_refs = {
        component.get("bom-ref")
        for component in components
        if isinstance(component.get("bom-ref"), str)
        and component["bom-ref"]
    }
    if (
        len(component_refs) != len(components)
        or any(
            not isinstance(component.get("purl"), str)
            or not component["purl"]
            for component in components
        )
    ):
        _fail("MAVEN_SBOM_ROOTFS", "rootfs component bom-ref closure differs")
    observed_identities = {
        (component.get("purl"), path)
        for component in components
        if isinstance(component.get("purl"), str)
        for path in _component_file_paths(component)
    }
    for component in components:
        paths = _component_file_paths(component)
        if not paths:
            _fail("MAVEN_SBOM_ROOTFS", "rootfs component file path is missing")
        for path in paths:
            nested_parent = _nested_jar_parent(path)
            if (
                _is_top_level_jar_path(path)
                and path in expected_paths
            ):
                continue
            if nested_parent in expected_paths:
                continue
            _fail(
                "MAVEN_SBOM_ROOTFS",
                f"rootfs component path escapes the closed JAR set: {path}",
            )
    added_refs: list[str] = []
    for record in files:
        if not isinstance(record, dict):
            _fail("MAVEN_SBOM", "Maven descriptor entry is malformed")
        path = record.get("path")
        if not isinstance(path, str) or not path.endswith(".jar"):
            continue
        parts = path.split("/")
        group = ".".join(parts[:-3])
        artifact, version = parts[-3:-1]
        purl = _maven_purl(path)
        if (purl, path) in observed_identities:
            continue
        bom_ref = (
            "urn:shirokuma:maven-jar-path:sha256:"
            + hashlib.sha256(path.encode("utf-8")).hexdigest()
        )
        if bom_ref in component_refs:
            _fail("MAVEN_SBOM", f"duplicate generated bom-ref: {bom_ref}")
        component_refs.add(bom_ref)
        added_refs.append(bom_ref)
        properties = [
            {
                "name": "aquasecurity:trivy:FilePath",
                "value": path,
            },
            {
                "name": "shirokuma:repository-origin",
                "value": record["repository_origin"],
            },
        ]
        if path in discovery_omissions:
            properties.append(
                {
                    "name": "shirokuma:rootfs-discovery",
                    "value": discovery_omissions[path],
                }
            )
        elif path in classifier_erased_discovery:
            properties.append(
                {
                    "name": "shirokuma:rootfs-discovery",
                    "value": "trivy-classifier-erased-purl",
                }
            )
        components.append(
            {
                "bom-ref": bom_ref,
                "type": "library",
                "group": group,
                "name": artifact,
                "version": version,
                "hashes": [
                    {"alg": "SHA-256", "content": record["sha256"]},
                ],
                "purl": purl,
                "properties": properties,
            }
        )
    generated_paths = {
        path
        for component in components
        for path in _component_file_paths(component)
        if _is_top_level_jar_path(path)
    }
    if generated_paths != expected_paths:
        _fail("MAVEN_SBOM", "generated top-level JAR set is not closed")
    rootfs_metadata = rootfs.get("metadata")
    root_component = (
        rootfs_metadata.get("component")
        if isinstance(rootfs_metadata, dict)
        else None
    )
    old_root_ref = (
        root_component.get("bom-ref")
        if isinstance(root_component, dict)
        else None
    )
    if not isinstance(old_root_ref, str) or old_root_ref in component_refs:
        _fail("MAVEN_SBOM_ROOTFS", "rootfs dependency root is invalid")
    dependencies = rootfs.get("dependencies")
    if not isinstance(dependencies, list):
        _fail("MAVEN_SBOM_ROOTFS", "rootfs dependencies are missing")
    dependency_map: dict[str, list[str]] = {}
    for dependency in dependencies:
        if (
            not isinstance(dependency, dict)
            or not isinstance(dependency.get("ref"), str)
            or not isinstance(dependency.get("dependsOn"), list)
            or not all(
                isinstance(target, str)
                for target in dependency["dependsOn"]
            )
            or dependency["ref"] in dependency_map
        ):
            _fail("MAVEN_SBOM_ROOTFS", "rootfs dependency graph is malformed")
        dependency_map[dependency["ref"]] = dependency["dependsOn"]
    external_refs = set(dependency_map) - component_refs
    if (
        external_refs not in (set(), {old_root_ref})
        or any(
            target not in component_refs
            for targets in dependency_map.values()
            for target in targets
        )
    ):
        _fail("MAVEN_SBOM_ROOTFS", "rootfs dependency references are not closed")
    generated_root_ref = "urn:shirokuma:maven-closure:483"
    generated_dependencies = [
        {
            "ref": generated_root_ref,
            "dependsOn": sorted(component_refs),
        },
        *[
            {
                "ref": component["bom-ref"],
                "dependsOn": dependency_map.get(component["bom-ref"], []),
            }
            for component in components
        ],
    ]
    nested_components = sum(
        any(_is_nested_jar_path(path) for path in _component_file_paths(component))
        for component in rootfs_components
    )
    output = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "component": {
                "bom-ref": generated_root_ref,
                "type": "application",
                "name": "manifest-derived-maven-closure",
            },
            "properties": [
                {
                    "name": "shirokuma:descriptor-sha256",
                    "value": _sha256(descriptor_path),
                },
                {
                    "name": "shirokuma:rootfs-discovered-jars",
                    "value": str(len(observed_rootfs)),
                },
                {
                    "name": "shirokuma:closed-manifest-jars",
                    "value": str(len(expected_paths)),
                },
                {
                    "name": "shirokuma:rootfs-discovered-components",
                    "value": str(len(rootfs_components)),
                },
                {
                    "name": "shirokuma:rootfs-discovered-nested-components",
                    "value": str(nested_components),
                },
                {
                    "name": "shirokuma:manifest-added-components",
                    "value": str(len(added_refs)),
                },
                {
                    "name": (
                        "shirokuma:rootfs-contract-authorized-omissions"
                    ),
                    "value": str(len(discovery_omissions)),
                },
                {
                    "name": (
                        "shirokuma:rootfs-contract-supplemental-jars"
                    ),
                    "value": str(
                        sum(
                            mode.startswith("contract-supplemental-")
                            for mode in discovery_omissions.values()
                        )
                    ),
                },
            ],
        },
        "components": components,
        "dependencies": generated_dependencies,
    }
    output_path.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _trivy_package_identities(
    report_path: Path,
    *,
    allow_high_critical: bool = False,
) -> set[tuple[str, str]]:
    report = _load_json(report_path)
    results = report.get("Results")
    if not isinstance(results, list) or not results:
        _fail("MAVEN_SCAN_REPORT", "Trivy report contains no results")
    identities: set[tuple[str, str]] = set()
    for result in results:
        if not isinstance(result, dict):
            _fail("MAVEN_SCAN_REPORT", "Trivy result is not an object")
        packages = result.get("Packages")
        if not isinstance(packages, list):
            continue
        for package in packages:
            if not isinstance(package, dict):
                _fail("MAVEN_SCAN_REPORT", "Trivy package is not an object")
            identifier = package.get("Identifier")
            purl = identifier.get("PURL") if isinstance(identifier, dict) else None
            if not isinstance(purl, str) or not purl:
                _fail("MAVEN_SCAN_REPORT", "Trivy package PURL is missing")
            path = package.get("FilePath")
            if not isinstance(path, str) or not path:
                _fail("MAVEN_SCAN_REPORT", "Trivy package file path is missing")
            identities.add((purl, path))
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            _fail("MAVEN_SCAN_REPORT", "Trivy vulnerabilities are malformed")
        if not allow_high_critical and any(
            isinstance(finding, dict)
            and finding.get("Severity") in {"HIGH", "CRITICAL"}
            for finding in vulnerabilities
        ):
            _fail("MAVEN_SCAN_FINDING", "Maven High/Critical finding remains")
    if not identities:
        _fail(
            "MAVEN_SCAN_REPORT",
            "Trivy report inventories no Maven package identities",
        )
    return identities


def _cyclonedx_components(
    sbom: Mapping[str, Any],
    sbom_path: Path,
) -> list[dict[str, Any]]:
    components = sbom.get("components")
    if (
        sbom.get("bomFormat") != "CycloneDX"
        or sbom.get("specVersion") != "1.7"
        or not isinstance(components, list)
        or not components
    ):
        _fail("MAVEN_SBOM", "CycloneDX document contains no components")
    if not all(isinstance(component, dict) for component in components):
        _fail("MAVEN_SBOM", f"{sbom_path} contains a malformed component")
    return components


def _component_file_paths(component: Mapping[str, Any]) -> set[str]:
    properties = component.get("properties", [])
    if not isinstance(properties, list):
        _fail("MAVEN_SBOM", "CycloneDX properties are malformed")
    paths: set[str] = set()
    for prop in properties:
        if (
            isinstance(prop, dict)
            and prop.get("name") == "aquasecurity:trivy:FilePath"
            and isinstance(prop.get("value"), str)
        ):
            paths.add(prop["value"])
    return paths


def _is_nested_jar_path(path: str) -> bool:
    return "!" in path or ".jar/" in path


def _nested_jar_parent(path: str) -> str | None:
    match = re.match(r"^(.+?\.jar)(?:!|/).+$", path)
    return match.group(1) if match is not None else None


def _is_top_level_jar_path(path: str) -> bool:
    return path.endswith(".jar") and not _is_nested_jar_path(path)


def _cyclonedx_top_level_jar_paths(
    sbom: Mapping[str, Any],
    sbom_path: Path,
) -> set[str]:
    components = _cyclonedx_components(sbom, sbom_path)
    paths = {
        path
        for component in components
        for path in _component_file_paths(component)
        if _is_top_level_jar_path(path)
    }
    if not paths:
        _fail("MAVEN_SBOM", "CycloneDX document inventories no top-level JARs")
    return paths


def verify_maven_scan(
    descriptor_path: Path,
    sbom_path: Path,
    report_path: Path,
    *,
    allow_high_critical: bool = False,
) -> None:
    expected_paths = _maven_jar_paths(descriptor_path)
    sbom = _load_json(sbom_path)
    observed_paths = _cyclonedx_top_level_jar_paths(sbom, sbom_path)
    if observed_paths != expected_paths:
        _fail(
            "MAVEN_SBOM_CLOSURE",
            (
                "CycloneDX JAR closure differs: "
                f"missing={sorted(expected_paths - observed_paths)!r}, "
                f"unexpected={sorted(observed_paths - expected_paths)!r}"
            ),
        )
    components = _cyclonedx_components(sbom, sbom_path)
    if any(
        not isinstance(component.get("purl"), str)
        or not component["purl"]
        for component in components
    ):
        _fail("MAVEN_SBOM_CLOSURE", "CycloneDX component PURL is missing")
    expected_identities: set[tuple[str, str]] = set()
    for component in components:
        paths = _component_file_paths(component)
        if not paths:
            _fail(
                "MAVEN_SBOM_CLOSURE",
                "CycloneDX component file path is missing",
            )
        expected_identities.update(
            (component["purl"], path) for path in paths
        )
    observed_identities = _trivy_package_identities(
        report_path,
        allow_high_critical=allow_high_critical,
    )
    if observed_identities != expected_identities:
        _fail(
            "MAVEN_SCAN_CLOSURE",
            (
                "Trivy report package identity closure differs: "
                "missing="
                f"{sorted(expected_identities - observed_identities)!r}, "
                "unexpected="
                f"{sorted(observed_identities - expected_identities)!r}"
            ),
        )


def _artifact_identity(reference: str) -> tuple[str, str]:
    expected_prefix = (
        "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@sha256:"
    )
    if not reference.startswith(expected_prefix):
        _fail("ARTIFACT_SUBJECT", "immutable artifact reference differs")
    digest = reference.removeprefix(
        "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@"
    )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        _fail("ARTIFACT_SUBJECT", "artifact digest is not exact lowercase sha256")
    return digest, digest.removeprefix("sha256:")


def _bind_cyclonedx(path: Path, reference: str, digest_hex: str) -> None:
    document = _load_json(path)
    metadata = document.get("metadata")
    if (
        document.get("bomFormat") != "CycloneDX"
        or document.get("specVersion") != "1.7"
        or not isinstance(metadata, dict)
    ):
        _fail("ARTIFACT_SBOM", f"{path} is not CycloneDX 1.7")
    previous = metadata.get("component")
    previous_ref = (
        previous.get("bom-ref")
        if isinstance(previous, dict)
        else None
    )
    source = previous.get("name") if isinstance(previous, dict) else None
    components = document.get("components")
    if (
        not isinstance(previous_ref, str)
        or not isinstance(components, list)
        or any(
            isinstance(component, dict)
            and component.get("bom-ref") in {previous_ref, reference}
            for component in components
        )
    ):
        _fail("ARTIFACT_SBOM", f"{path} dependency root is invalid")

    def rebind(value: Any) -> Any:
        if isinstance(value, str):
            return reference if value == previous_ref else value
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, dict):
            return {key: rebind(item) for key, item in value.items()}
        return value

    document = rebind(document)
    metadata = document["metadata"]
    metadata["component"] = {
        "bom-ref": reference,
        "type": "file",
        "name": "shirokuma-trino-maven-dependencies",
        "version": EXPECTED_TAG,
        "hashes": [{"alg": "SHA-256", "content": digest_hex}],
        "properties": [
            {"name": "shirokuma:artifact-reference", "value": reference},
            {"name": "shirokuma:scan-source", "value": source or "unknown"},
        ],
    }
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, list):
        _fail("ARTIFACT_SBOM", f"{path} dependency graph is missing")
    dependency_refs = {
        dependency.get("ref")
        for dependency in dependencies
        if isinstance(dependency, dict)
        and isinstance(dependency.get("ref"), str)
    }
    if len(dependency_refs) != len(dependencies):
        _fail("ARTIFACT_SBOM", f"{path} dependency graph is malformed")
    if reference not in dependency_refs:
        component_refs = {
            component["bom-ref"]
            for component in components
            if isinstance(component, dict)
            and isinstance(component.get("bom-ref"), str)
        }
        if len(component_refs) != len(components):
            _fail("ARTIFACT_SBOM", f"{path} component references differ")
        dependencies.append(
            {
                "ref": reference,
                "dependsOn": sorted(component_refs),
            }
        )
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _bind_trivy_report(path: Path, reference: str, digest: str) -> None:
    document = _load_json(path)
    if not _matches_exact_json(document.get("SchemaVersion"), 2):
        _fail("ARTIFACT_SCAN", f"{path} is not a Trivy v2 report")
    previous = document.get("ArtifactName")
    metadata = document.get("Metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        document["Metadata"] = metadata
    document["ArtifactName"] = reference
    metadata["ImageID"] = digest
    metadata["RepoDigests"] = [reference]
    metadata["ScanSource"] = previous
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_cyclonedx_subject(path: Path, reference: str, digest_hex: str) -> None:
    document = _load_json(path)
    metadata = document.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(component, dict):
        _fail("ARTIFACT_SBOM", f"{path} subject is missing")
    expected = {
        "bom-ref": reference,
        "type": "file",
        "name": "shirokuma-trino-maven-dependencies",
        "version": EXPECTED_TAG,
        "hashes": [{"alg": "SHA-256", "content": digest_hex}],
    }
    if any(component.get(key) != value for key, value in expected.items()):
        _fail("ARTIFACT_SBOM", f"{path} subject differs")
    properties = component.get("properties")
    if (
        not isinstance(properties, list)
        or {"name": "shirokuma:artifact-reference", "value": reference}
        not in properties
    ):
        _fail("ARTIFACT_SBOM", f"{path} immutable reference is missing")
    components = document.get("components")
    dependencies = document.get("dependencies")
    if not isinstance(components, list) or not isinstance(dependencies, list):
        _fail("ARTIFACT_SBOM", f"{path} dependency graph is missing")
    component_refs = {
        component.get("bom-ref")
        for component in components
        if isinstance(component, dict)
        and isinstance(component.get("bom-ref"), str)
    }
    if len(component_refs) != len(components) or reference in component_refs:
        _fail("ARTIFACT_SBOM", f"{path} component references differ")
    dependency_map: dict[str, list[str]] = {}
    for dependency in dependencies:
        if (
            not isinstance(dependency, dict)
            or not isinstance(dependency.get("ref"), str)
            or not isinstance(dependency.get("dependsOn"), list)
            or not all(
                isinstance(target, str)
                for target in dependency["dependsOn"]
            )
            or dependency["ref"] in dependency_map
        ):
            _fail("ARTIFACT_SBOM", f"{path} dependency graph is malformed")
        dependency_map[dependency["ref"]] = dependency["dependsOn"]
    if (
        set(dependency_map) - component_refs != {reference}
        or any(
            target not in component_refs
            for targets in dependency_map.values()
            for target in targets
        )
    ):
        _fail("ARTIFACT_SBOM", f"{path} dependency references are not closed")
    reachable: set[str] = set()
    pending = list(dependency_map.get(reference, []))
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(dependency_map.get(current, []))
    if reachable != component_refs:
        _fail("ARTIFACT_SBOM", f"{path} components are not root-reachable")


def _verify_trivy_subject(path: Path, reference: str, digest: str) -> None:
    document = _load_json(path)
    metadata = document.get("Metadata")
    if (
        document.get("ArtifactName") != reference
        or not isinstance(metadata, dict)
        or metadata.get("ImageID") != digest
        or metadata.get("RepoDigests") != [reference]
    ):
        _fail("ARTIFACT_SCAN", f"{path} subject differs")


def bind_artifact_evidence(
    reference: str,
    descriptor_path: Path,
    maven_sbom_path: Path,
    maven_report_path: Path,
    bun_sbom_path: Path,
    bun_raw_report_path: Path,
    bun_adjusted_report_path: Path,
) -> None:
    verify_maven_scan(descriptor_path, maven_sbom_path, maven_report_path)
    digest, digest_hex = _artifact_identity(reference)
    for path in (maven_sbom_path, bun_sbom_path):
        _bind_cyclonedx(path, reference, digest_hex)
    for path in (
        maven_report_path,
        bun_raw_report_path,
        bun_adjusted_report_path,
    ):
        _bind_trivy_report(path, reference, digest)
    for path in (maven_sbom_path, bun_sbom_path):
        _verify_cyclonedx_subject(path, reference, digest_hex)
    for path in (
        maven_report_path,
        bun_raw_report_path,
        bun_adjusted_report_path,
    ):
        _verify_trivy_subject(path, reference, digest)


def _parse_time(value: str) -> dt.datetime:
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        _fail("AUTHORIZATION_TIME", str(error))
    if result.tzinfo != dt.timezone.utc:
        _fail("AUTHORIZATION_TIME", "authorization timestamps must be UTC")
    return result


def _validate_authorization(
    contract: Mapping[str, Any], *, at: dt.datetime | None
) -> None:
    authorization = contract.get("authorization")
    if not _matches_exact_json(authorization, EXPECTED_AUTHORIZATION):
        _fail("AUTHORIZATION", "Issue #63 authorization record differs")
    approved = _parse_time(authorization.get("approved_at", ""))
    expires = _parse_time(authorization.get("expires_at", ""))
    if expires - approved > dt.timedelta(days=30) or approved >= expires:
        _fail("AUTHORIZATION", "time-boxed Issue #63 authorization differs")
    if at is not None and not approved <= at < expires:
        _fail(
            "AUTHORIZATION_EXPIRED",
            f"{at.isoformat()} is outside [{approved.isoformat()}, {expires.isoformat()})",
        )


def authorize_use(
    root: Path,
    *,
    validation_point: str,
    at: dt.datetime | None = None,
) -> None:
    contract = _load_json(root / CONTRACT_PATH)
    instant = at or dt.datetime.now(dt.timezone.utc)
    _validate_authorization(contract, at=instant)
    if validation_point not in contract["authorization"]["validation_points"]:
        _fail(
            "AUTHORIZATION",
            f"unrecognized validation point: {validation_point}",
        )
    if validation_point == "before_dependency_publication":
        _validate_publication_attempt(contract, environment=os.environ)


def _validate_publication_attempt(
    contract: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    publication = contract.get("publication")
    if (
        not isinstance(publication, Mapping)
        or not _matches_exact_json(
            publication.get("reauthorization"),
            EXPECTED_PUBLICATION_REAUTHORIZATION,
        )
        or not _matches_exact_json(
            publication.get("authorized_attempt"),
            EXPECTED_PUBLICATION_ATTEMPT,
        )
    ):
        _fail(
            "PUBLICATION_ATTEMPT",
            "single authorized publication attempt binding differs",
        )
    if environment is None:
        return
    reauthorization = publication["reauthorization"]
    if (
        reauthorization.get("status") != "active"
        or reauthorization.get("publication_authorized_after_required_approval")
        is not True
        or reauthorization.get("next_sequence_authorized") is not True
    ):
        _fail("PUBLICATION_ATTEMPT", "no publication attempt is authorized")
    observed = {
        "event_name": environment.get("GITHUB_EVENT_NAME"),
        "ref": environment.get("GITHUB_REF"),
        "before_sha": environment.get("GITHUB_EVENT_BEFORE"),
        "run_attempt": environment.get("GITHUB_RUN_ATTEMPT"),
    }
    if not _matches_exact_json(observed, EXPECTED_PUBLICATION_ATTEMPT):
        _fail(
            "PUBLICATION_ATTEMPT",
            f"workflow attempt is not authorized: {observed!r}",
        )


def _validate_independent_review_contract(contract: Mapping[str, Any]) -> None:
    publication = contract.get("publication")
    if (
        not isinstance(publication, Mapping)
        or not _matches_exact_json(
            publication.get("independent_review"),
            EXPECTED_INDEPENDENT_REVIEW,
        )
        or not _matches_exact_json(
            publication.get("owner_only_approval_exception"),
            EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION,
        )
    ):
        _fail(
            "INDEPENDENT_REVIEW",
            "publication approval contract differs",
        )


def _github_api_json(
    url: str,
    *,
    token: str,
    request_payload: Mapping[str, Any] | None = None,
    maximum_response_bytes: int = GITHUB_API_RESPONSE_BYTES,
) -> Any:
    if not token:
        _fail("INDEPENDENT_REVIEW", "GitHub token is missing")
    if (
        type(maximum_response_bytes) is not int
        or maximum_response_bytes < GITHUB_API_RESPONSE_BYTES
        or maximum_response_bytes > GITHUB_COMMENT_PAGE_RESPONSE_BYTES
    ):
        _fail("INDEPENDENT_REVIEW", "GitHub API response bound differs")
    data = None
    if request_payload is not None:
        data = json.dumps(
            request_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "shirokuma-independent-review-verifier",
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            if getattr(response, "status", None) != 200:
                _fail(
                    "INDEPENDENT_REVIEW",
                    f"GitHub API status differs: {response.status}",
                )
            response_payload = response.read(maximum_response_bytes + 1)
    except URLError as error:
        _fail("INDEPENDENT_REVIEW", f"GitHub API request failed: {error}")
    if len(response_payload) > maximum_response_bytes:
        _fail("INDEPENDENT_REVIEW", "GitHub API response is too large")
    try:
        return json.loads(response_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail("INDEPENDENT_REVIEW", f"GitHub API JSON is invalid: {error}")


def _github_api_list(url: str, *, token: str) -> list[Any]:
    result = _github_api_json(url, token=token)
    if not isinstance(result, list):
        _fail("INDEPENDENT_REVIEW", "GitHub API response must be a list")
    if len(result) >= 100:
        _fail("INDEPENDENT_REVIEW", "GitHub API result may be truncated")
    return result


def _pull_binding_policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    expected = EXPECTED_PENDING_REVIEW_REPAIR["pull_request_binding"]
    if not _matches_exact_json(
        policy,
        expected,
    ):
        _fail("INDEPENDENT_REVIEW", "pull-request binding policy differs")
    return policy


def _pull_binding_snapshot(pull: Any) -> tuple[Any, ...]:
    if not isinstance(pull, Mapping):
        _fail("INDEPENDENT_REVIEW", "associated pull request is malformed")
    number = pull.get("number")
    base = pull.get("base")
    head = pull.get("head")
    if (
        type(number) is not int
        or number <= 0
        or not isinstance(pull.get("state"), str)
        or not isinstance(pull.get("merged_at"), str)
        or not isinstance(pull.get("merge_commit_sha"), str)
        or not isinstance(base, Mapping)
        or not isinstance(head, Mapping)
        or not isinstance(base.get("ref"), str)
        or not isinstance(base.get("sha"), str)
        or not isinstance(base.get("repo"), Mapping)
        or not isinstance(base["repo"].get("full_name"), str)
        or not isinstance(head.get("ref"), str)
        or not isinstance(head.get("sha"), str)
        or not isinstance(head.get("repo"), Mapping)
        or not isinstance(head["repo"].get("full_name"), str)
    ):
        _fail("INDEPENDENT_REVIEW", "associated pull-request identity is malformed")
    return (
        number,
        pull["state"],
        pull["merged_at"],
        pull["merge_commit_sha"],
        base["ref"],
        base["sha"],
        base["repo"]["full_name"],
        head["ref"],
        head["sha"],
        head["repo"]["full_name"],
    )


def _github_pull_binding_pages(
    policy: Mapping[str, Any],
    *,
    query: str,
    token: str,
    commit: str | None = None,
    head_ref: str | None = None,
) -> list[Any]:
    policy = _pull_binding_policy(policy)
    api_by_query = {
        "merge_commit": "rest_merge_commit_associated_pulls",
        "final_head": "rest_final_head_associated_pulls",
        "head_ref": "rest_head_filtered_pulls",
    }
    if query not in api_by_query or api_by_query[query] not in policy["apis"]:
        _fail("INDEPENDENT_REVIEW", "pull-request binding query differs")
    if query in {"merge_commit", "final_head"}:
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            _fail("INDEPENDENT_REVIEW", "pull-request binding commit is not exact")
        base = (
            "https://api.github.com/repos/TommyKammy/Shirokuma/commits/"
            f"{quote(commit, safe='')}/pulls"
        )
        fixed_query: dict[str, Any] = {}
    else:
        if not isinstance(head_ref, str) or not head_ref or ":" in head_ref:
            _fail("INDEPENDENT_REVIEW", "pull-request head ref is not exact")
        base = "https://api.github.com/repos/TommyKammy/Shirokuma/pulls"
        fixed_query = {
            "state": "all",
            "head": f"TommyKammy:{head_ref}",
        }

    def scan() -> tuple[list[Any], tuple[Any, ...]]:
        pulls: list[Any] = []
        snapshot: list[Any] = []
        for page in range(1, GITHUB_PULL_BINDING_MAXIMUM_PAGES + 1):
            page_url = base + "?" + urlencode(
                {
                    **fixed_query,
                    "per_page": GITHUB_PULL_BINDING_PAGE_SIZE,
                    "page": page,
                }
            )
            page_pulls = _github_api_json(
                page_url,
                token=token,
                maximum_response_bytes=GITHUB_PULL_BINDING_PAGE_RESPONSE_BYTES,
            )
            if (
                not isinstance(page_pulls, list)
                or len(page_pulls) > GITHUB_PULL_BINDING_PAGE_SIZE
            ):
                _fail(
                    "INDEPENDENT_REVIEW",
                    "paginated pull-request binding response is malformed",
                )
            for pull in page_pulls:
                snapshot.append(_pull_binding_snapshot(pull))
            pulls.extend(page_pulls)
            if len(pulls) > GITHUB_PULL_BINDING_MAXIMUM_PULL_REQUESTS:
                _fail("INDEPENDENT_REVIEW", "pull-request binding bound exceeded")
            if len(page_pulls) < GITHUB_PULL_BINDING_PAGE_SIZE:
                return pulls, tuple(snapshot)
        _fail(
            "INDEPENDENT_REVIEW",
            "pull-request binding exhaustion was not proven within the accepted bound",
        )

    _first_pulls, first_snapshot = scan()
    second_pulls, second_snapshot = scan()
    if first_snapshot != second_snapshot:
        _fail("INDEPENDENT_REVIEW", "pull-request binding snapshot is unstable")
    if len(second_pulls) != 1:
        _fail(
            "INDEPENDENT_REVIEW",
            f"exact associated pull-request count differs: {len(second_pulls)}",
        )
    return second_pulls


def _validate_pull_binding_identity(
    pull: Any,
    *,
    pull_request: int,
    merge_commit: str,
    final_head: str,
    expected_base_sha: str,
) -> str:
    _pull_binding_snapshot(pull)
    base = pull["base"]
    head = pull["head"]
    if (
        pull.get("number") != pull_request
        or type(pull_request) is not int
        or pull_request <= 0
        or pull.get("state") != "closed"
        or pull.get("merge_commit_sha") != merge_commit
        or re.fullmatch(r"[0-9a-f]{40}", merge_commit) is None
        or _parse_time(pull["merged_at"]) <= dt.datetime.min.replace(
            tzinfo=dt.timezone.utc
        )
        or base.get("ref") != "main"
        or base.get("sha") != expected_base_sha
        or re.fullmatch(r"[0-9a-f]{40}", expected_base_sha) is None
        or base["repo"].get("full_name") != "TommyKammy/Shirokuma"
        or head.get("sha") != final_head
        or re.fullmatch(r"[0-9a-f]{40}", final_head) is None
        or head["repo"].get("full_name") != "TommyKammy/Shirokuma"
        or not head.get("ref")
    ):
        _fail("INDEPENDENT_REVIEW", "exact pull-request binding differs")
    return str(head["ref"])


def _github_exact_pull_binding(
    policy: Mapping[str, Any],
    *,
    pull_request: int,
    merge_commit: str,
    final_head: str,
    expected_base_sha: str,
    token: str,
) -> dict[str, Any]:
    merge_pull = _github_pull_binding_pages(
        policy,
        query="merge_commit",
        commit=merge_commit,
        token=token,
    )[0]
    head_ref = _validate_pull_binding_identity(
        merge_pull,
        pull_request=pull_request,
        merge_commit=merge_commit,
        final_head=final_head,
        expected_base_sha=expected_base_sha,
    )
    final_head_pull = _github_pull_binding_pages(
        policy,
        query="final_head",
        commit=final_head,
        token=token,
    )[0]
    head_filtered_pull = _github_pull_binding_pages(
        policy,
        query="head_ref",
        head_ref=head_ref,
        token=token,
    )[0]
    identities = {
        _pull_binding_snapshot(merge_pull),
        _pull_binding_snapshot(final_head_pull),
        _pull_binding_snapshot(head_filtered_pull),
    }
    if len(identities) != 1:
        _fail("INDEPENDENT_REVIEW", "pull-request binding APIs disagree")
    for pull in (final_head_pull, head_filtered_pull):
        if (
            _validate_pull_binding_identity(
                pull,
                pull_request=pull_request,
                merge_commit=merge_commit,
                final_head=final_head,
                expected_base_sha=expected_base_sha,
            )
            != head_ref
        ):
            _fail("INDEPENDENT_REVIEW", "pull-request head ref differs")
    return {
        "pull_request": pull_request,
        "merge_commit": merge_commit,
        "final_head": final_head,
        "base_sha": expected_base_sha,
        "head_ref": head_ref,
        "apis": list(_pull_binding_policy(policy)["apis"]),
    }


def _github_api_paginated_reviews(
    url: str,
    *,
    token: str,
    policy: Mapping[str, Any] | None = None,
) -> list[Any]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or re.fullmatch(
            r"/repos/TommyKammy/Shirokuma/pulls/[1-9][0-9]*/reviews",
            parsed.path,
        )
        is None
        or parsed.fragment
        or parsed.query
    ):
        _fail("INDEPENDENT_REVIEW", "GitHub pull-review pagination URL differs")
    review_policy = (
        EXPECTED_INDEPENDENT_REVIEW["reviews"] if policy is None else policy
    )
    if (
        not isinstance(review_policy, Mapping)
        or review_policy.get("api") != "rest_pull_request_reviews"
        or review_policy.get("page_size") != GITHUB_PULL_REVIEW_PAGE_SIZE
        or review_policy.get("maximum_pages")
        != GITHUB_PULL_REVIEW_MAXIMUM_PAGES
        or review_policy.get("maximum_reviews")
        != GITHUB_PULL_REVIEW_MAXIMUM_REVIEWS
        or review_policy.get("maximum_page_bytes")
        != GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES
        or review_policy.get("stable_snapshot_passes") != 2
        or review_policy.get("unique_review_ids") is not True
        or review_policy.get("pagination_must_be_complete") is not True
    ):
        _fail("INDEPENDENT_REVIEW", "GitHub pull-review pagination policy differs")

    def scan() -> tuple[list[Any], tuple[tuple[Any, ...], ...]]:
        reviews: list[Any] = []
        review_ids: set[int] = set()
        decision_snapshot: list[tuple[Any, ...]] = []
        for page in range(1, GITHUB_PULL_REVIEW_MAXIMUM_PAGES + 1):
            page_url = urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urlencode(
                        {
                            "per_page": GITHUB_PULL_REVIEW_PAGE_SIZE,
                            "page": page,
                        }
                    ),
                    "",
                )
            )
            result = _github_api_json(
                page_url,
                token=token,
                maximum_response_bytes=GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES,
            )
            if not isinstance(result, list) or len(result) > GITHUB_PULL_REVIEW_PAGE_SIZE:
                _fail(
                    "INDEPENDENT_REVIEW",
                    "paginated GitHub pull-review response is malformed",
                )
            for review in result:
                review_id = review.get("id") if isinstance(review, Mapping) else None
                if (
                    type(review_id) is not int
                    or review_id <= 0
                    or review_id in review_ids
                ):
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "GitHub pull-review IDs are invalid or duplicated",
                    )
                review_ids.add(review_id)
                user = review.get("user")
                decision_snapshot.append(
                    (
                        review_id,
                        review.get("state"),
                        review.get("commit_id"),
                        review.get("submitted_at"),
                        user.get("login") if isinstance(user, Mapping) else None,
                        user.get("type") if isinstance(user, Mapping) else None,
                    )
                )
            reviews.extend(result)
            if len(reviews) > GITHUB_PULL_REVIEW_MAXIMUM_REVIEWS:
                _fail("INDEPENDENT_REVIEW", "GitHub pull-review bound exceeded")
            if len(result) < GITHUB_PULL_REVIEW_PAGE_SIZE:
                return reviews, tuple(decision_snapshot)
        _fail("INDEPENDENT_REVIEW", "GitHub pull-review result may be truncated")

    _, first_snapshot = scan()
    second_reviews, second_snapshot = scan()
    if second_snapshot != first_snapshot:
        _fail("INDEPENDENT_REVIEW", "GitHub pull-review snapshot is unstable")
    return second_reviews


def _github_api_paginated_list(
    url: str,
    *,
    token: str,
    page_size: int = 100,
    maximum_pages: int = 10,
    maximum_items: int = GITHUB_OWNER_COMMENT_MAXIMUM_ITEMS,
    maximum_page_bytes: int = GITHUB_COMMENT_PAGE_RESPONSE_BYTES,
    stable_snapshot_passes: int = 2,
) -> list[Any]:
    if (
        type(page_size) is not int
        or page_size != 100
        or type(maximum_pages) is not int
        or maximum_pages != 10
        or type(maximum_items) is not int
        or maximum_items != page_size * maximum_pages - 1
        or maximum_page_bytes != GITHUB_COMMENT_PAGE_RESPONSE_BYTES
        or stable_snapshot_passes != 2
    ):
        _fail("INDEPENDENT_REVIEW", "GitHub pagination bounds differ")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or parsed.path
        != "/repos/TommyKammy/Shirokuma/issues/148/comments"
        or parsed.fragment
        or parsed.query
    ):
        _fail("INDEPENDENT_REVIEW", "GitHub pagination URL differs")

    def scan() -> tuple[list[Any], tuple[Any, ...]]:
        items: list[Any] = []
        snapshot: list[Any] = []
        previous_id = 0
        for page in range(1, maximum_pages + 1):
            page_url = urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urlencode({"per_page": page_size, "page": page}),
                    "",
                )
            )
            result = _github_api_json(
                page_url,
                token=token,
                maximum_response_bytes=maximum_page_bytes,
            )
            if not isinstance(result, list) or len(result) > page_size:
                _fail(
                    "INDEPENDENT_REVIEW",
                    "paginated GitHub API response is malformed",
                )
            for item in result:
                comment_id = item.get("id") if isinstance(item, Mapping) else None
                if type(comment_id) is not int or comment_id <= previous_id:
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "GitHub comment IDs are not strictly increasing",
                    )
                previous_id = comment_id
                user = item.get("user")
                snapshot.append(
                    (
                        comment_id,
                        item.get("body"),
                        user.get("login") if isinstance(user, Mapping) else None,
                        user.get("type") if isinstance(user, Mapping) else None,
                        item.get("author_association"),
                        item.get("created_at"),
                        item.get("updated_at"),
                    )
                )
            items.extend(result)
            if len(items) > maximum_items:
                _fail("INDEPENDENT_REVIEW", "GitHub pagination bound exceeded")
            if len(result) != page_size:
                return items, tuple(snapshot)
        _fail(
            "INDEPENDENT_REVIEW",
            "GitHub comment exhaustion was not proven within the accepted bound",
        )

    first_items, first_snapshot = scan()
    second_items, second_snapshot = scan()
    if second_snapshot != first_snapshot:
        _fail("INDEPENDENT_REVIEW", "GitHub comment snapshot is unstable")
    return second_items


def _github_api_mapping(
    url: str,
    *,
    token: str,
    request_payload: Mapping[str, Any] | None = None,
    maximum_response_bytes: int = GITHUB_API_RESPONSE_BYTES,
) -> Mapping[str, Any]:
    result = _github_api_json(
        url,
        token=token,
        request_payload=request_payload,
        maximum_response_bytes=maximum_response_bytes,
    )
    if not isinstance(result, Mapping):
        _fail("INDEPENDENT_REVIEW", "GitHub API response must be an object")
    return result


def _active_owner_exception(
    contract: Mapping[str, Any],
) -> Mapping[str, Any]:
    publication = contract.get("publication")
    if not isinstance(publication, Mapping):
        _fail("INDEPENDENT_REVIEW", "publication approval contract differs")
    exception = publication.get("owner_only_approval_exception")
    reauthorization = publication.get("reauthorization")
    if not isinstance(exception, Mapping) or exception.get("status") != "active":
        _fail("INDEPENDENT_REVIEW", "owner approval exception is not active")
    if "consumed_run" in exception:
        _fail("INDEPENDENT_REVIEW", "consumed owner approval exception cannot be reused")
    if (
        publication.get("permitted") is not True
        or not isinstance(reauthorization, Mapping)
        or reauthorization.get("status") != "active"
        or reauthorization.get("publication_authorized_after_required_approval")
        is not True
        or reauthorization.get("next_sequence_authorized") is not True
    ):
        _fail("INDEPENDENT_REVIEW", "owner approval authorization is not active")
    return exception


def _owner_pull_base_sha(
    contract: Mapping[str, Any],
    pull: Mapping[str, Any],
) -> str:
    publication = contract.get("publication")
    authorized_attempt = (
        publication.get("authorized_attempt")
        if isinstance(publication, Mapping)
        else None
    )
    base = pull.get("base")
    expected_base_sha = (
        authorized_attempt.get("before_sha")
        if isinstance(authorized_attempt, Mapping)
        else None
    )
    observed_base_sha = base.get("sha") if isinstance(base, Mapping) else None
    if (
        not isinstance(expected_base_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", expected_base_sha) is None
        or observed_base_sha != expected_base_sha
        or not isinstance(observed_base_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", observed_base_sha) is None
    ):
        _fail("INDEPENDENT_REVIEW", "owner pull-request base SHA differs")
    return expected_base_sha


def _select_independent_review(
    contract: Mapping[str, Any],
    pulls: list[Any],
    reviews: list[Any],
    *,
    commit: str,
    comments: list[Any] | Callable[[], list[Any]] | None = None,
) -> dict[str, Any]:
    _validate_independent_review_contract(contract)
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        _fail("INDEPENDENT_REVIEW", "publisher commit is not an exact SHA")
    matching_pulls = [
        pull
        for pull in pulls
        if isinstance(pull, Mapping)
        and pull.get("state") == "closed"
        and pull.get("merged_at")
        and pull.get("merge_commit_sha") == commit
        and isinstance(pull.get("base"), Mapping)
        and pull["base"].get("ref") == "main"
        and isinstance(pull.get("number"), int)
    ]
    if len(matching_pulls) != 1:
        _fail(
            "INDEPENDENT_REVIEW",
            f"exact merged pull request count differs: {len(matching_pulls)}",
        )
    pull = matching_pulls[0]
    head = pull.get("head")
    final_head = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(final_head, str) or re.fullmatch(
        r"[0-9a-f]{40}", final_head
    ) is None:
        _fail("INDEPENDENT_REVIEW", "final pull request head is not exact")
    merged_at = pull.get("merged_at")
    if not isinstance(merged_at, str):
        _fail("INDEPENDENT_REVIEW", "merged pull request timestamp is missing")
    merged_instant = _parse_time(merged_at)

    latest_by_reviewer: dict[str, Mapping[str, Any]] = {}
    for review in reviews:
        if not isinstance(review, Mapping):
            _fail("INDEPENDENT_REVIEW", "review entry must be an object")
        user = review.get("user")
        if not isinstance(user, Mapping) or not isinstance(user.get("login"), str):
            _fail("INDEPENDENT_REVIEW", "review user identity is missing")
        if review.get("state") not in {"COMMENTED", "PENDING"}:
            latest_by_reviewer[user["login"].casefold()] = review

    remediation = contract.get("source", {}).get("build_plugin_remediation", {})
    risk_owner = str(remediation.get("risk_owner", "")).casefold()
    implementation_author = str(
        remediation.get("implementation_author", "")
    ).casefold()
    qualified = []
    for login, review in latest_by_reviewer.items():
        user = review["user"]
        if (
            review.get("state") == "APPROVED"
            and user.get("type") == EXPECTED_INDEPENDENT_REVIEW["human_user_type"]
            and login not in {risk_owner, implementation_author}
            and isinstance(review.get("id"), int)
            and review.get("commit_id") == final_head
        ):
            submitted_at = review.get("submitted_at")
            if not isinstance(submitted_at, str):
                continue
            if _parse_time(submitted_at) < merged_instant:
                qualified.append(review)
    minimum_approvals = EXPECTED_INDEPENDENT_REVIEW["minimum_approved_reviews"]
    if len(qualified) >= minimum_approvals:
        selected = sorted(qualified, key=lambda review: int(review["id"]))[0]
        return {
            "approval_mode": "independent_review",
            "pull_request": pull["number"],
            "review_id": selected["id"],
            "reviewer": selected["user"]["login"],
            "reviewed_head": final_head,
            "commit": commit,
        }
    if comments is None:
        _fail(
            "INDEPENDENT_REVIEW",
            (
                "no current human approval differs from the risk owner and "
                "author, and no owner final-head attestation is available"
            ),
        )

    # This is the exact user-authorized policy alternative for PR #148's
    # fifth attempt, not a generic self-review fallback for another PR/run.
    exception = _active_owner_exception(contract)
    if (
        exception["scope"] != "pr_148_fifth_publication_attempt_only"
        or exception["reason"] != "sole_owner_personal_experimental_project"
        or exception["repository"] != "TommyKammy/Shirokuma"
        or exception["pull_request"] != pull["number"]
        or exception["standard_independent_review_remains_accepted"] is not True
    ):
        _fail("INDEPENDENT_REVIEW", "owner exception scope differs")
    owner_comments = comments() if callable(comments) else comments
    return _select_owner_final_head_attestation(
        contract,
        pull,
        owner_comments,
        commit=commit,
        final_head=final_head,
    )


def _select_owner_final_head_attestation(
    contract: Mapping[str, Any],
    pull: Mapping[str, Any],
    comments: list[Any],
    *,
    commit: str,
    final_head: str,
) -> dict[str, Any]:
    _validate_independent_review_contract(contract)
    exception = _active_owner_exception(contract)
    if pull.get("number") != exception["pull_request"]:
        _fail("INDEPENDENT_REVIEW", "owner exception pull request differs")
    base_sha = _owner_pull_base_sha(contract, pull)
    merged_at = pull.get("merged_at")
    if not isinstance(merged_at, str):
        _fail("INDEPENDENT_REVIEW", "merged pull request timestamp is missing")
    merged_instant = _parse_time(merged_at)
    template = exception["attestation_body_template"]
    marker = "Owner final-head attestation for PR #148\n\nDecision: "
    pattern = re.compile(
        r"\AOwner final-head attestation for PR #148\n\n"
        r"Decision: (APPROVED|REVOKED)\n"
        r"Final head: ([0-9a-f]{40})\n"
        r"Exception: https://github\.com/TommyKammy/Shirokuma/issues/63"
        r"#issuecomment-5268936554\Z"
    )
    decisions: list[
        tuple[int, str, str, dt.datetime, dt.datetime, Mapping[str, Any]]
    ] = []
    for comment in comments:
        if not isinstance(comment, Mapping):
            _fail("INDEPENDENT_REVIEW", "pull request comment must be an object")
        body = comment.get("body")
        if not isinstance(body, str) or not body.startswith(marker):
            continue
        user = comment.get("user")
        if (
            not isinstance(user, Mapping)
            or user.get("login") != exception["owner"]
            or user.get("type") != exception["human_user_type"]
            or comment.get("author_association")
            != exception["author_association"]
        ):
            continue
        match = pattern.fullmatch(body)
        if (
            match is None
            or type(comment.get("id")) is not int
            or comment["id"] <= 0
            or not isinstance(comment.get("created_at"), str)
            or not isinstance(comment.get("updated_at"), str)
        ):
            _fail("INDEPENDENT_REVIEW", "owner attestation comment is malformed")
        decision, attested_head = match.groups()
        if decision not in exception["allowed_decisions"]:
            _fail("INDEPENDENT_REVIEW", "owner attestation decision differs")
        if body != template.format(
            decision=decision,
            final_head=attested_head,
        ):
            _fail("INDEPENDENT_REVIEW", "owner attestation body differs")
        created_at = _parse_time(comment["created_at"])
        updated_at = _parse_time(comment["updated_at"])
        if updated_at < created_at:
            _fail("INDEPENDENT_REVIEW", "owner attestation timestamps differ")
        decisions.append(
            (
                int(comment["id"]),
                decision,
                attested_head,
                created_at,
                updated_at,
                comment,
            )
        )
    if not decisions:
        _fail("INDEPENDENT_REVIEW", "owner final-head attestation is missing")
    latest_updated_at = max(item[4] for item in decisions)
    latest = [item for item in decisions if item[4] == latest_updated_at]
    if len(latest) != 1:
        _fail("INDEPENDENT_REVIEW", "latest owner attestation is ambiguous")
    (
        comment_id,
        decision,
        attested_head,
        created_at,
        updated_at,
        selected,
    ) = latest[0]
    exception_approved_at = _parse_time(exception["approved_at"])
    if (
        decision != "APPROVED"
        or attested_head != final_head
        or created_at < exception_approved_at
        or updated_at >= merged_instant
    ):
        _fail(
            "INDEPENDENT_REVIEW",
            "latest owner attestation is not a pre-merge approval of final head",
        )
    return {
        "approval_mode": "owner_final_head_attestation",
        "pull_request": pull["number"],
        "comment_id": comment_id,
        "owner": selected["user"]["login"],
        "attested_head": attested_head,
        "attested_at": updated_at.isoformat().replace("+00:00", "Z"),
        "commit": commit,
        "base_sha": base_sha,
    }


def _revalidate_owner_final_head_decision(
    contract: Mapping[str, Any],
    pull: Mapping[str, Any],
    selection: Mapping[str, Any],
    comments: list[Any],
) -> dict[str, bool]:
    revalidated = _select_owner_final_head_attestation(
        contract,
        pull,
        comments,
        commit=selection["commit"],
        final_head=selection["attested_head"],
    )
    original = {
        key: selection[key]
        for key in (
            "approval_mode",
            "pull_request",
            "comment_id",
            "owner",
            "attested_head",
            "attested_at",
            "commit",
            "base_sha",
        )
    }
    if revalidated != original:
        _fail(
            "INDEPENDENT_REVIEW",
            "owner final-head decision changed after final API gates",
        )
    return {"owner_decision_revalidated_after_final_api_gates": True}


def _validate_owner_final_head_ci(
    exception: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    association_policy: Mapping[str, Any],
    pull_request: int,
    final_head: str,
    head_ref: str,
    attested_at: dt.datetime,
) -> dict[str, Any]:
    _pull_binding_policy(association_policy)
    policy = exception["final_head_ci"]
    if policy.get("same_path_run_selection") != (
        "latest_qualifying_success_attestation_preceding_by_updated_created_id"
    ):
        _fail("INDEPENDENT_REVIEW", "final-head workflow selection policy differs")
    runs = payload.get("workflow_runs")
    total_count = payload.get("total_count")
    if (
        not isinstance(runs, list)
        or type(total_count) is not int
        or total_count != len(runs)
        or len(runs) > GITHUB_WORKFLOW_RUN_MAXIMUM_RUNS
    ):
        _fail(
            "INDEPENDENT_REVIEW",
            "final-head workflow run response is truncated or malformed",
        )

    required_paths = policy["workflow_paths"]
    candidates: dict[
        str, list[tuple[dt.datetime, dt.datetime, int, Mapping[str, Any]]]
    ] = {path: [] for path in required_paths}
    observed_required_run_ids: set[int] = set()
    for run in runs:
        if not isinstance(run, Mapping) or not isinstance(run.get("path"), str):
            _fail("INDEPENDENT_REVIEW", "workflow run entry is malformed")
        path = run["path"]
        if path not in required_paths:
            continue
        repository = run.get("repository")
        head_repository = run.get("head_repository")
        pull_requests = run.get("pull_requests")
        if (
            not isinstance(repository, Mapping)
            or repository.get("full_name") != exception["repository"]
            or not isinstance(head_repository, Mapping)
            or head_repository.get("full_name") != exception["repository"]
            or not isinstance(pull_requests, list)
            or len(pull_requests) >= 100
        ):
            _fail(
                "INDEPENDENT_REVIEW",
                f"final-head workflow identity differs: {path}",
            )
        observed_pull_requests: list[int] = []
        for associated_pull in pull_requests:
            if (
                not isinstance(associated_pull, Mapping)
                or type(associated_pull.get("number")) is not int
            ):
                _fail(
                    "INDEPENDENT_REVIEW",
                    f"workflow pull-request binding is malformed: {path}",
                )
            observed_pull_requests.append(associated_pull["number"])
        created_at = run.get("created_at")
        updated_at = run.get("updated_at")
        run_id = run.get("id")
        if not isinstance(created_at, str) or not isinstance(updated_at, str):
            _fail(
                "INDEPENDENT_REVIEW",
                f"workflow completion timestamp is missing: {path}",
            )
        created_instant = _parse_time(created_at)
        updated_instant = _parse_time(updated_at)
        if (
            run.get("head_sha") != final_head
            or run.get("head_branch") != head_ref
            or run.get("event") != policy["event"]
            or observed_pull_requests not in ([], [pull_request])
            or type(run_id) is not int
            or run_id <= 0
            or created_instant > updated_instant
        ):
            _fail(
                "INDEPENDENT_REVIEW",
                f"required final-head workflow did not pass before attestation: {path}",
            )
        if run_id in observed_required_run_ids:
            _fail("INDEPENDENT_REVIEW", "final-head workflow run ID is duplicated")
        observed_required_run_ids.add(run_id)
        if (
            updated_instant >= attested_at
            or run.get("status") != policy["status"]
            or run.get("conclusion") != policy["conclusion"]
        ):
            continue
        candidates[path].append((updated_instant, created_instant, run_id, run))

    selected: dict[str, Mapping[str, Any]] = {}
    for path in required_paths:
        if not candidates[path]:
            _fail(
                "INDEPENDENT_REVIEW",
                f"required final-head workflow is missing: {path}",
            )
        _, _, _, latest = max(
            candidates[path],
            key=lambda candidate: (candidate[0], candidate[1], candidate[2]),
        )
        selected[path] = latest
    return {
        "workflow_runs": {
            path: int(selected[path]["id"]) for path in required_paths
        },
        "completed_before_attestation": True,
    }


def _github_workflow_run_pages(
    exception: Mapping[str, Any],
    *,
    association_policy: Mapping[str, Any],
    pull_request: int,
    final_head: str,
    head_ref: str,
    token: str,
) -> Mapping[str, Any]:
    policy = exception["final_head_ci"]
    if (
        policy.get("api") != "rest_workflow_runs"
        or policy.get("page_size") != GITHUB_WORKFLOW_RUN_PAGE_SIZE
        or policy.get("maximum_pages") != GITHUB_WORKFLOW_RUN_MAXIMUM_PAGES
        or policy.get("maximum_runs") != GITHUB_WORKFLOW_RUN_MAXIMUM_RUNS
        or policy.get("maximum_page_bytes")
        != GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES
        or policy.get("stable_snapshot_passes") != 2
        or policy.get("same_path_run_selection")
        != "latest_qualifying_success_attestation_preceding_by_updated_created_id"
        or policy.get("positive_unique_run_ids") is not True
        or policy.get("total_count_must_match") is not True
        or policy.get("pagination_must_be_complete") is not True
    ):
        _fail("INDEPENDENT_REVIEW", "final-head workflow pagination policy differs")
    _pull_binding_policy(association_policy)
    if (
        type(pull_request) is not int
        or pull_request != exception["pull_request"]
        or not isinstance(final_head, str)
        or re.fullmatch(r"[0-9a-f]{40}", final_head) is None
        or not isinstance(head_ref, str)
        or not head_ref
    ):
        _fail("INDEPENDENT_REVIEW", "final-head workflow query identity differs")

    base = "https://api.github.com/repos/TommyKammy/Shirokuma/actions/runs"

    def scan() -> tuple[list[Any], tuple[Any, ...], int]:
        runs: list[Any] = []
        snapshot: list[Any] = []
        observed_run_ids: set[int] = set()
        observed_total_count: int | None = None
        for page in range(1, GITHUB_WORKFLOW_RUN_MAXIMUM_PAGES + 1):
            page_url = (
                f"{base}?"
                + urlencode(
                    {
                        "event": policy["event"],
                        "head_sha": final_head,
                        "branch": head_ref,
                        "per_page": GITHUB_WORKFLOW_RUN_PAGE_SIZE,
                        "page": page,
                    }
                )
            )
            payload = _github_api_mapping(
                page_url,
                token=token,
                maximum_response_bytes=GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES,
            )
            page_runs = payload.get("workflow_runs")
            total_count = payload.get("total_count")
            if (
                not isinstance(page_runs, list)
                or len(page_runs) > GITHUB_WORKFLOW_RUN_PAGE_SIZE
                or type(total_count) is not int
                or total_count < 0
                or total_count > GITHUB_WORKFLOW_RUN_MAXIMUM_RUNS
            ):
                _fail(
                    "INDEPENDENT_REVIEW",
                    "paginated final-head workflow response is malformed",
                )
            if observed_total_count is None:
                observed_total_count = total_count
            elif total_count != observed_total_count:
                _fail(
                    "INDEPENDENT_REVIEW",
                    "final-head workflow total count changed between pages",
                )
            for run in page_runs:
                run_id = run.get("id") if isinstance(run, Mapping) else None
                repository = run.get("repository") if isinstance(run, Mapping) else None
                head_repository = (
                    run.get("head_repository") if isinstance(run, Mapping) else None
                )
                pull_requests = (
                    run.get("pull_requests") if isinstance(run, Mapping) else None
                )
                if (
                    type(run_id) is not int
                    or run_id <= 0
                    or run_id in observed_run_ids
                    or not isinstance(repository, Mapping)
                    or not isinstance(head_repository, Mapping)
                    or not isinstance(pull_requests, list)
                ):
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "final-head workflow run identity is malformed or duplicated",
                    )
                observed_run_ids.add(run_id)
                pull_binding = []
                for associated_pull in pull_requests:
                    associated_number = (
                        associated_pull.get("number")
                        if isinstance(associated_pull, Mapping)
                        else None
                    )
                    if type(associated_number) is not int or associated_number <= 0:
                        _fail(
                            "INDEPENDENT_REVIEW",
                            "workflow pull-request binding is malformed",
                        )
                    pull_binding.append(associated_number)
                if pull_binding not in ([], [pull_request]):
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "workflow pull-request binding differs",
                    )
                if run.get("head_branch") != head_ref:
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "workflow head branch differs",
                    )
                snapshot.append(
                    (
                        run_id,
                        run.get("path"),
                        run.get("head_sha"),
                        run.get("head_branch"),
                        run.get("event"),
                        run.get("status"),
                        run.get("conclusion"),
                        run.get("created_at"),
                        run.get("updated_at"),
                        repository.get("full_name"),
                        head_repository.get("full_name"),
                        tuple(pull_binding),
                    )
                )
            runs.extend(page_runs)
            if len(runs) > GITHUB_WORKFLOW_RUN_MAXIMUM_RUNS:
                _fail("INDEPENDENT_REVIEW", "final-head workflow run bound exceeded")
            if len(page_runs) < GITHUB_WORKFLOW_RUN_PAGE_SIZE:
                if len(runs) != observed_total_count:
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "final-head workflow total count differs from collected pages",
                    )
                return runs, tuple(snapshot), observed_total_count
        _fail(
            "INDEPENDENT_REVIEW",
            "final-head workflow exhaustion was not proven within the accepted bound",
        )

    _first_runs, first_snapshot, first_total_count = scan()
    second_runs, second_snapshot, second_total_count = scan()
    if (
        second_total_count != first_total_count
        or second_snapshot != first_snapshot
    ):
        _fail("INDEPENDENT_REVIEW", "final-head workflow snapshot is unstable")
    return {"total_count": second_total_count, "workflow_runs": second_runs}


def _owner_review_thread_page(
    payload: Mapping[str, Any],
    *,
    pull_request: int,
    final_head: str,
) -> tuple[list[Mapping[str, Any]], int, bool, str | None]:
    if payload.get("errors") not in (None, []):
        _fail("INDEPENDENT_REVIEW", "review-thread GraphQL query failed")
    data = payload.get("data")
    repository = data.get("repository") if isinstance(data, Mapping) else None
    pull = (
        repository.get("pullRequest")
        if isinstance(repository, Mapping)
        else None
    )
    connection = (
        pull.get("reviewThreads") if isinstance(pull, Mapping) else None
    )
    nodes = connection.get("nodes") if isinstance(connection, Mapping) else None
    page_info = (
        connection.get("pageInfo") if isinstance(connection, Mapping) else None
    )
    total_count = (
        connection.get("totalCount")
        if isinstance(connection, Mapping)
        else None
    )
    has_next_page = (
        page_info.get("hasNextPage")
        if isinstance(page_info, Mapping)
        else None
    )
    end_cursor = (
        page_info.get("endCursor")
        if isinstance(page_info, Mapping)
        else None
    )
    if (
        not isinstance(repository, Mapping)
        or repository.get("nameWithOwner") != "TommyKammy/Shirokuma"
        or not isinstance(pull, Mapping)
        or type(pull.get("number")) is not int
        or pull["number"] != pull_request
        or pull.get("headRefOid") != final_head
        or not isinstance(connection, Mapping)
        or type(total_count) is not int
        or total_count < 0
        or total_count > GITHUB_REVIEW_THREAD_MAXIMUM_THREADS
        or not isinstance(nodes, list)
        or len(nodes) > GITHUB_REVIEW_THREAD_PAGE_SIZE
        or not isinstance(page_info, Mapping)
        or type(has_next_page) is not bool
        or (
            end_cursor is not None
            and (not isinstance(end_cursor, str) or not end_cursor)
        )
        or (has_next_page and not isinstance(end_cursor, str))
        or (has_next_page and not nodes)
    ):
        _fail(
            "INDEPENDENT_REVIEW",
            "review-thread GraphQL response is truncated or malformed",
        )

    page_thread_ids: set[str] = set()
    validated_nodes: list[Mapping[str, Any]] = []
    for thread in nodes:
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if (
            not isinstance(thread, Mapping)
            or not isinstance(thread_id, str)
            or not thread_id
            or thread_id in page_thread_ids
            or type(thread.get("isResolved")) is not bool
            or type(thread.get("isOutdated")) is not bool
        ):
            _fail("INDEPENDENT_REVIEW", "review-thread entry is malformed")
        page_thread_ids.add(thread_id)
        validated_nodes.append(thread)
    return validated_nodes, total_count, has_next_page, end_cursor


def _owner_review_thread_receipt(
    exception: Mapping[str, Any],
    nodes: list[Mapping[str, Any]],
) -> dict[str, int]:
    unresolved = 0
    outdated = 0
    resolved = 0
    for thread in nodes:
        if thread["isOutdated"]:
            outdated += 1
        elif thread["isResolved"]:
            resolved += 1
        else:
            unresolved += 1
    current_non_outdated = resolved + unresolved
    expected = exception["review_threads"]["current_non_outdated"]
    if current_non_outdated != expected:
        _fail(
            "INDEPENDENT_REVIEW",
            (
                "current non-outdated review-thread count differs: "
                f"{current_non_outdated}"
            ),
        )
    return {
        "total": len(nodes),
        "current_non_outdated": current_non_outdated,
        "current_unresolved": unresolved,
        "resolved": resolved,
        "outdated": outdated,
    }


def _validate_owner_review_threads(
    exception: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    pull_request: int,
    final_head: str,
) -> dict[str, int]:
    nodes, total_count, has_next_page, _ = _owner_review_thread_page(
        payload,
        pull_request=pull_request,
        final_head=final_head,
    )
    if has_next_page or total_count != len(nodes):
        _fail(
            "INDEPENDENT_REVIEW",
            "review-thread GraphQL response is truncated or malformed",
        )
    return _owner_review_thread_receipt(exception, nodes)


def _github_review_threads_pages(
    exception: Mapping[str, Any],
    *,
    pull_request: int,
    final_head: str,
    token: str,
) -> dict[str, int]:
    policy = exception.get("review_threads")
    if (
        not isinstance(policy, Mapping)
        or policy.get("page_size") != GITHUB_REVIEW_THREAD_PAGE_SIZE
        or policy.get("maximum_pages")
        != GITHUB_REVIEW_THREAD_MAXIMUM_PAGES
        or policy.get("maximum_threads")
        != GITHUB_REVIEW_THREAD_MAXIMUM_THREADS
        or policy.get("stable_snapshot_passes") != 2
        or policy.get("unique_thread_ids") is not True
        or policy.get("total_count_must_match") is not True
        or policy.get("pagination_must_be_complete") is not True
    ):
        _fail("INDEPENDENT_REVIEW", "review-thread pagination bounds differ")

    query = (
        "query($owner:String!,$name:String!,$number:Int!,$after:String){"
        "repository(owner:$owner,name:$name){nameWithOwner "
        "pullRequest(number:$number){number headRefOid "
        "reviewThreads(first:100,after:$after){totalCount "
        "nodes{id isResolved isOutdated}"
        "pageInfo{hasNextPage endCursor}}}}}"
    )

    def scan() -> tuple[
        list[Mapping[str, Any]], tuple[tuple[Any, ...], ...], int
    ]:
        after: str | None = None
        observed_cursors: set[str] = set()
        observed_thread_ids: set[str] = set()
        observed_total_count: int | None = None
        all_nodes: list[Mapping[str, Any]] = []
        snapshot: list[tuple[Any, ...]] = []
        for _page in range(GITHUB_REVIEW_THREAD_MAXIMUM_PAGES):
            payload = _github_api_mapping(
                "https://api.github.com/graphql",
                token=token,
                request_payload={
                    "query": query,
                    "variables": {
                        "owner": "TommyKammy",
                        "name": "Shirokuma",
                        "number": pull_request,
                        "after": after,
                    },
                },
            )
            nodes, total_count, has_next_page, end_cursor = (
                _owner_review_thread_page(
                    payload,
                    pull_request=pull_request,
                    final_head=final_head,
                )
            )
            if after is not None and not nodes:
                _fail(
                    "INDEPENDENT_REVIEW",
                    "review-thread continuation page is empty",
                )
            if observed_total_count is None:
                observed_total_count = total_count
            elif total_count != observed_total_count:
                _fail(
                    "INDEPENDENT_REVIEW",
                    "review-thread total count changed between pages",
                )
            for thread in nodes:
                thread_id = thread["id"]
                if thread_id in observed_thread_ids:
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "review-thread ID is duplicated between pages",
                    )
                observed_thread_ids.add(thread_id)
                all_nodes.append(thread)
                snapshot.append(
                    (
                        thread_id,
                        thread["isResolved"],
                        thread["isOutdated"],
                    )
                )
            if len(all_nodes) > GITHUB_REVIEW_THREAD_MAXIMUM_THREADS:
                _fail("INDEPENDENT_REVIEW", "review-thread bound exceeded")
            if not has_next_page:
                if len(all_nodes) != observed_total_count:
                    _fail(
                        "INDEPENDENT_REVIEW",
                        "review-thread total count differs from collected pages",
                    )
                _owner_review_thread_receipt(exception, all_nodes)
                return all_nodes, tuple(snapshot), observed_total_count
            if not isinstance(end_cursor, str):
                _fail(
                    "INDEPENDENT_REVIEW",
                    "review-thread continuation cursor is missing",
                )
            if end_cursor in observed_cursors:
                _fail("INDEPENDENT_REVIEW", "review-thread cursor repeated")
            observed_cursors.add(end_cursor)
            after = end_cursor
        _fail("INDEPENDENT_REVIEW", "review-thread result may be truncated")

    _first_nodes, first_snapshot, first_total_count = scan()
    second_nodes, second_snapshot, second_total_count = scan()
    if (
        second_total_count != first_total_count
        or second_snapshot != first_snapshot
    ):
        _fail("INDEPENDENT_REVIEW", "review-thread snapshot is unstable")
    return _owner_review_thread_receipt(exception, second_nodes)


def _verify_owner_final_head_gates(
    exception: Mapping[str, Any],
    selection: Mapping[str, Any],
    *,
    association_policy: Mapping[str, Any],
    token: str,
) -> dict[str, Any]:
    pull_request = selection["pull_request"]
    final_head = selection["attested_head"]
    attested_at = _parse_time(selection["attested_at"])
    pull_request_binding = _github_exact_pull_binding(
        association_policy,
        pull_request=pull_request,
        merge_commit=selection["commit"],
        final_head=final_head,
        expected_base_sha=selection["base_sha"],
        token=token,
    )
    head_ref = pull_request_binding["head_ref"]
    workflow_payload = _github_workflow_run_pages(
        exception,
        association_policy=association_policy,
        pull_request=pull_request,
        final_head=final_head,
        head_ref=head_ref,
        token=token,
    )
    final_head_ci = _validate_owner_final_head_ci(
        exception,
        workflow_payload,
        association_policy=association_policy,
        pull_request=pull_request,
        final_head=final_head,
        head_ref=head_ref,
        attested_at=attested_at,
    )
    review_threads = _github_review_threads_pages(
        exception,
        pull_request=pull_request,
        final_head=final_head,
        token=token,
    )
    return {
        "pull_request_binding": pull_request_binding,
        "final_head_ci": final_head_ci,
        "review_threads": review_threads,
    }


def verify_independent_review(
    root: Path,
    *,
    repository: str,
    commit: str,
    token: str,
) -> None:
    root = root.resolve()
    if repository != "TommyKammy/Shirokuma":
        _fail("INDEPENDENT_REVIEW", "repository identity differs")
    contract = _load_json(root / CONTRACT_PATH)
    _validate_independent_review_contract(contract)
    publication = contract.get("publication", {})
    if publication.get("permitted") is not True:
        _fail(
            "INDEPENDENT_REVIEW",
            "publication reauthorization is pending",
        )
    pending_repair = publication.get("pending_review_repair")
    if not _matches_exact_json(pending_repair, EXPECTED_PENDING_REVIEW_REPAIR):
        _fail("INDEPENDENT_REVIEW", "pending review repair contract differs")
    association_policy = pending_repair["pull_request_binding"]
    base = "https://api.github.com/repos/TommyKammy/Shirokuma"
    exception = contract["publication"]["owner_only_approval_exception"]
    pulls = _github_pull_binding_pages(
        association_policy,
        query="merge_commit",
        commit=commit,
        token=token,
    )
    matching = pulls
    reviews = _github_api_paginated_reviews(
        f"{base}/pulls/{matching[0]['number']}/reviews",
        token=token,
        policy=contract["publication"]["independent_review"]["reviews"],
    )
    review_selection = _select_independent_review(
        contract,
        pulls,
        reviews,
        commit=commit,
        comments=lambda: _github_api_paginated_list(
            f"{base}/issues/{matching[0]['number']}/comments",
            token=token,
        ),
    )
    if review_selection["approval_mode"] == "owner_final_head_attestation":
        exception = contract["publication"]["owner_only_approval_exception"]
        review_selection.update(
            _verify_owner_final_head_gates(
                exception,
                review_selection,
                association_policy=association_policy,
                token=token,
            )
        )
        owner_comment_policy = exception["owner_issue_comments"]
        if (
            owner_comment_policy.get("revalidate_after_final_api_gates") is not True
            or owner_comment_policy.get("revalidated_decision_must_match") is not True
        ):
            _fail("INDEPENDENT_REVIEW", "owner decision revalidation policy differs")
        review_selection.update(
            _revalidate_owner_final_head_decision(
                contract,
                matching[0],
                review_selection,
                _github_api_paginated_list(
                    f"{base}/issues/{matching[0]['number']}/comments",
                    token=token,
                ),
            )
        )
    print(json.dumps(review_selection, sort_keys=True))


def _workflow_jobs_and_steps(workflow: str) -> tuple[list[str], dict[str, list[str]]]:
    jobs: list[str] = []
    steps: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in workflow.splitlines():
        if line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        job = re.fullmatch(r"  ([a-z][a-z0-9_-]*):", line)
        if job:
            current = job.group(1)
            jobs.append(current)
            steps[current] = []
            continue
        step = re.fullmatch(r"      - name: (.+)", line)
        if step and current is not None:
            steps[current].append(step.group(1))
    return jobs, steps


def _maven_command_before_marker(
    workflow: str,
    output_marker: str,
    *,
    code: str,
    network_none: bool,
    required_mounts: tuple[str, ...] = (),
    docker_option_fragment_counts: tuple[tuple[str, int], ...] = (),
) -> str:
    docker_marker = "docker run --rm \\\n"
    maven_marker = (
        "  --entrypoint /usr/share/maven/bin/mvn \\\n"
        '  "${BUILDER_IMAGE}" \\\n'
    )
    if workflow.count(output_marker) != 1:
        _fail(code, f"output marker differs: {output_marker}")
    end = workflow.index(output_marker)
    docker_start = workflow.rfind(docker_marker, 0, end)
    if docker_start < 0:
        _fail(code, "builder invocation is missing")
    line_start = workflow.rfind("\n", 0, docker_start) + 1
    block = textwrap.dedent(workflow[line_start:end])
    observed_network_none = block.count("  --network none \\\n")
    docker_options = block.split(maven_marker, 1)[0]
    if (
        block.count(maven_marker) != 1
        or observed_network_none != (1 if network_none else 0)
        or block.count(f"  {EXPECTED_SETTINGS_MOUNT} \\\n") != 1
        or any(
            docker_options.count(f"  {mount} \\\n") != 1
            for mount in required_mounts
        )
        or any(
            docker_options.count(fragment) != expected_count
            for fragment, expected_count in docker_option_fragment_counts
        )
    ):
        _fail(code, "Maven builder invocation differs")
    arguments = block.split(maven_marker, 1)[1]
    normalized = " ".join(arguments.replace("\\\n", " ").split())
    if not normalized:
        _fail(code, "Maven arguments are missing")
    return f"mvn {normalized}"


def _offline_maven_command(workflow: str) -> str:
    output_marker = (
        "            python3 scripts/"
        "verify_trino_maven_feasibility.py verify-candidate \\"
    )
    loop_marker = "          for suffix in a b; do\n"
    docker_marker = "            docker run --rm \\\n"
    if workflow.count(output_marker) != 1:
        _fail("WORKFLOW_OFFLINE_COMMAND", "output marker differs")
    end = workflow.index(output_marker)
    loop_start = workflow.rfind(loop_marker, 0, end)
    docker_start = workflow.rfind(docker_marker, 0, end)
    if loop_start < 0 or docker_start <= loop_start:
        _fail(
            "WORKFLOW_OFFLINE_COMMAND",
            "offline repository preparation is missing",
        )
    preparation = textwrap.dedent(workflow[loop_start:docker_start])
    if preparation != EXPECTED_OFFLINE_MAVEN_REPOSITORY_PREPARATION:
        _fail(
            "WORKFLOW_OFFLINE_COMMAND",
            "offline repository preparation differs",
        )
    return _maven_command_before_marker(
        workflow,
        output_marker,
        code="WORKFLOW_OFFLINE_COMMAND",
        network_none=True,
        required_mounts=(EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT,),
        docker_option_fragment_counts=((":/m2", 1), ("--mount ", 0)),
    )


def _resolution_maven_commands(workflow: str) -> tuple[str, str]:
    commands = [
        _maven_command_before_marker(
            workflow,
            f'            2>&1 | tee "${{candidate}}/maven-transfer-{suffix}.log"',
            code="WORKFLOW_RESOLUTION_COMMAND",
            network_none=False,
        )
        for suffix in ("a", "b")
    ]
    return commands[0], commands[1]


def _validate_settings(root: Path) -> None:
    path = root / SETTINGS_PATH
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as error:
        _fail("SETTINGS", str(error))
    root_element = tree.getroot()
    namespace = {"m": "http://maven.apache.org/SETTINGS/1.2.0"}
    for forbidden in ("servers", "proxies", "pluginGroups"):
        if root_element.find(f"m:{forbidden}", namespace) is not None:
            _fail("SETTINGS", f"{forbidden} is forbidden")
    mirror_containers = root_element.findall("m:mirrors", namespace)
    mirrors = root_element.findall(".//m:mirror", namespace)
    if (
        len(mirror_containers) != 1
        or len(mirrors) != len(EXPECTED_REPOSITORY_MIRRORS)
        or list(mirror_containers[0]) != mirrors
    ):
        _fail("SETTINGS", "exactly three closed repository mirrors are required")
    for mirror, expected in zip(mirrors, EXPECTED_REPOSITORY_MIRRORS):
        mirror_values = tuple(
            (
                child.tag.rsplit("}", 1)[-1],
                (child.text or "").strip(),
            )
            for child in mirror
        )
        if (
            mirror.attrib
            or (mirror.text or "").strip()
            or mirror_values != expected
            or any(
                child.attrib
                or list(child)
                or (child.tail or "").strip()
                for child in mirror
            )
        ):
            _fail("SETTINGS", "closed repository mirror set differs")
    repositories: dict[str, str] = {}
    for repository in root_element.findall(".//m:repository", namespace):
        repository_id = repository.findtext("m:id", namespaces=namespace)
        url = repository.findtext("m:url", namespaces=namespace)
        if not repository_id or not url or repository_id in repositories:
            _fail("SETTINGS", "repository ids and URLs must be present and unique")
        repositories[repository_id] = url
        snapshots = repository.find("m:snapshots", namespace)
        if (
            snapshots is None
            or snapshots.findtext("m:enabled", namespaces=namespace) != "false"
        ):
            _fail("SETTINGS", "snapshot repositories are forbidden")
    if repositories != EXPECTED_REPOSITORIES:
        _fail("SETTINGS", f"repository allowlist differs: {repositories!r}")
    plugin_repositories = root_element.findall(".//m:pluginRepository", namespace)
    if len(plugin_repositories) != 1:
        _fail("SETTINGS", "exactly one Central plugin repository is required")
    plugin_id = plugin_repositories[0].findtext("m:id", namespaces=namespace)
    plugin_url = plugin_repositories[0].findtext("m:url", namespaces=namespace)
    if (plugin_id, plugin_url) != ("central", EXPECTED_REPOSITORIES["central"]):
        _fail("SETTINGS", "plugin repository must be exact Maven Central")
    for element in root_element.iter():
        local = element.tag.rsplit("}", 1)[-1].lower()
        if any(token in local for token in ("password", "username", "token")):
            _fail("SETTINGS", f"credential element is forbidden: {local}")


def audit_builder_settings(path: Path) -> None:
    """Accept only inert containers and Maven's exact default HTTP blocker."""
    namespace = "http://maven.apache.org/SETTINGS/1.2.0"
    try:
        root = ET.fromstring(
            _read_reviewed_regular_file(
                path,
                code="BUILDER_SETTINGS",
                max_bytes=MAX_BUILDER_SETTINGS_BYTES,
            )
        )
    except ET.ParseError as error:
        _fail("BUILDER_SETTINGS", str(error))
    if root.tag != f"{{{namespace}}}settings":
        _fail("BUILDER_SETTINGS", f"unexpected root element: {root.tag}")
    if (root.text or "").strip():
        _fail("BUILDER_SETTINGS", "settings root contains non-whitespace text")

    observed: set[str] = set()
    for element in root:
        name = element.tag.rsplit("}", 1)[-1]
        if (
            name not in ALLOWED_GLOBAL_SETTINGS_CONTAINERS
            or element.tag != f"{{{namespace}}}{name}"
        ):
            _fail("BUILDER_SETTINGS", f"active or unknown element: {name}")
        if name in observed:
            _fail("BUILDER_SETTINGS", f"duplicate container: {name}")
        children = list(element)
        if name == "mirrors":
            mirror = children[0] if len(children) == 1 else None
            values = (
                tuple(
                    (
                        child.tag.rsplit("}", 1)[-1],
                        (child.text or "").strip(),
                    )
                    for child in mirror
                )
                if mirror is not None
                else ()
            )
            if (
                mirror is None
                or mirror.tag != f"{{{namespace}}}mirror"
                or mirror.attrib
                or (mirror.text or "").strip()
                or (mirror.tail or "").strip()
                or values != DEFAULT_HTTP_BLOCKER
                or any(
                    child.tag
                    != f"{{{namespace}}}{expected_name}"
                    or child.attrib
                    or list(child)
                    or (child.tail or "").strip()
                    for child, (expected_name, _) in zip(
                        mirror, DEFAULT_HTTP_BLOCKER
                    )
                )
            ):
                _fail("BUILDER_SETTINGS", "default HTTP blocker differs")
            children = []
        if (
            element.attrib
            or children
            or (element.text or "").strip()
            or (element.tail or "").strip()
        ):
            _fail("BUILDER_SETTINGS", f"non-empty container: {name}")
        observed.add(name)
    if observed != ALLOWED_GLOBAL_SETTINGS_CONTAINERS:
        _fail(
            "BUILDER_SETTINGS",
            f"global settings container set differs: {sorted(observed)!r}",
        )


def _validate_workflow(contract: Mapping[str, Any], workflow: str) -> None:
    _validate_publication_attempt(contract)
    _validate_independent_review_contract(contract)
    jobs, steps = _workflow_jobs_and_steps(workflow)
    lines = workflow.splitlines()
    if jobs != ["validate", "publish"] or steps != EXPECTED_STEPS:
        _fail("WORKFLOW_CLOSED_WORLD", f"jobs={jobs!r}, steps={steps!r}")
    if (
        lines.count("  pull_request:") != 1
        or lines.count("  push:") != 1
        or lines.count("      - main") != 1
        or "pull_request_target" in workflow
        or "workflow_dispatch" in workflow
    ):
        _fail("WORKFLOW_TRIGGER", "only PR validation and main push are allowed")
    if (
        workflow.count("GITHUB_EVENT_BEFORE: ${{ github.event.before }}") != 1
        or workflow.count(
            "--validation-point before_dependency_publication"
        )
        != 3
        or workflow.count("GITHUB_TOKEN: ${{ github.token }}") != 2
        or workflow.count("verify-independent-review") != 2
        or workflow.count("verify-independent-review --root .") != 1
        or workflow.count("final_review_gate=(") != 1
        or workflow.count('"${final_review_gate[@]}"') != 1
        or lines.count("      actions: read") != 1
        or lines.count("      issues: read") != 1
        or lines.count("      pull-requests: read") != 1
    ):
        _fail(
            "WORKFLOW_PUBLICATION_ATTEMPT",
            "main publication must consume the single exact push attempt",
        )
    if (
        workflow.count(EXPECTED_PR_SOURCE_CONDITION) != 3
        or workflow.count(EXPECTED_PR_BUN_INPUT_BLOCK) != 1
        or any(
            workflow.count(marker) != 1
            for marker in EXPECTED_PR_OVERLAY_BUILD_MARKERS
        )
    ):
        _fail(
            "WORKFLOW_PR_OVERLAY_VALIDATION",
            (
                "blocked pull requests must remain static-only; authorized pull "
                "requests must fetch, apply, and build the exact Web UI overlay "
                "without enabling publication"
            ),
        )
    if (
        lines.count("permissions:") != 1
        or lines.count("    permissions:") != 2
        or lines.count("  contents: read") != 1
        or lines.count("      contents: read") != 2
        or lines.count("      packages: write") != 1
        or lines.count("      id-token: write") != 1
        or "contents: write" in workflow
        or "actions: write" in workflow
        or "secrets." in workflow
    ):
        _fail("WORKFLOW_PERMISSIONS", "minimal closed permissions differ")
    action_counts: dict[str, int] = {}
    for action in ACTION_RE.findall(workflow):
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}", action) is None:
            _fail("WORKFLOW_ACTION", f"action is not pinned to a full SHA: {action}")
        action_counts[action] = action_counts.get(action, 0) + 1
    if action_counts != EXPECTED_ACTIONS:
        _fail("WORKFLOW_ACTION", f"closed action set differs: {action_counts!r}")
    for path in (
        WORKFLOW_PATH,
        CONTRACT_PATH,
        ADMISSION_PATH,
        JVM_CONFIG_PATH,
        SETTINGS_PATH,
        PACKAGER_PATH,
        BUN_PACKAGER_PATH,
        BUN_PREPARER_PATH,
        PARQUET_REMEDIATION_PATH,
        VERIFIER_PATH,
        FEASIBILITY_VERIFIER_PATH,
        TEST_PATH,
        FEASIBILITY_TEST_PATH,
        BUN_TEST_PATH,
        PARQUET_REMEDIATION_TEST_PATH,
        SOURCE_OVERLAY_PATH,
        DISTRIBUTION_REMEDIATION_PATH,
        BUILD_PLUGIN_REMEDIATION_PATH,
        VEX_PATH,
        OVERLAY_ADR_PATH,
        PARQUET_REMEDIATION_ADR_PATH,
        DISTRIBUTION_REMEDIATION_ADR_PATH,
        BLOCKER_ADR_PATH,
        BUILD_PLUGIN_REMEDIATION_ADR_PATH,
        AUTHORIZED_FEASIBILITY_RECORD_PATH,
        AUTHORIZED_FEASIBILITY_RECEIPT_PATH,
        Path("Makefile"),
    ):
        if lines.count(f"      - {path.as_posix()}") != 2:
            _fail("WORKFLOW_PATHS", f"{path} must trigger PR and main publication")
    required = (
        "github.repository == 'TommyKammy/Shirokuma'",
        "github.event_name == 'push'",
        "github.ref == 'refs/heads/main'",
        "github.sha == github.workflow_sha",
        'test "${GITHUB_REPOSITORY}" = "TommyKammy/Shirokuma"',
        'test "${GITHUB_SHA}" = "${GITHUB_WORKFLOW_SHA}"',
        "ubuntu-24.04-arm",
        'test "${RUNNER_ARCH}" = "ARM64"',
        'test "$(uname -m)" = "aarch64"',
        "--network none",
        EXPECTED_SOURCE_REPOSITORY,
        EXPECTED_PARQUET_SOURCE_REMEDIATION["repository"],
        EXPECTED_PARQUET_SOURCE_REMEDIATION["commit_sha"],
        EXPECTED_PARQUET_SOURCE_REMEDIATION["tree_sha"],
        EXPECTED_PARQUET_SOURCE_REMEDIATION["release_tag_object"],
        EXPECTED_PARQUET_SOURCE_REMEDIATION["nested_rc_tag_object"],
        EXPECTED_COMMIT,
        EXPECTED_TREE,
        EXPECTED_TAG_OBJECT,
        EXPECTED_BUILDER,
        "--entrypoint /usr/share/maven/bin/mvn",
        "--env MAVEN_CONFIG=/tmp/maven-home/.m2",
        "--workdir /policy",
        "--file /workspace/pom.xml",
        "python3 scripts/verify_trino_dependency_publisher.py authorize",
        "python3 scripts/verify_trino_dependency_publisher.py authorize-use",
        "verify-independent-review --root .",
        "python3 scripts/verify_trino_maven_feasibility.py verify-candidate",
        "python3 scripts/verify_trino_maven_feasibility.py prune-vulnerable-inputs",
        "publication-status --root .",
        (
            "Trino dependency publication is blocked pending "
            "new explicit owner authorization"
        ),
        "python3 scripts/verify_trino_dependency_publisher.py audit-builder-settings",
        "python3 scripts/verify_trino_dependency_publisher.py audit-transfer-log",
        "python3 scripts/remediate_parquet_jackson.py prepare-source",
        "python3 scripts/remediate_parquet_jackson.py stage-artifact",
        "python3 scripts/remediate_parquet_jackson.py seal-artifact",
        "python3 scripts/remediate_parquet_jackson.py compare-artifacts",
        "generate-maven-sbom",
        "verify-maven-scan",
        "bind-artifact-evidence",
        "scan-type: rootfs",
        "prune-reactor-outputs",
        "python3 scripts/package_trino_maven_dependencies.py create",
        "python3 scripts/package_trino_maven_dependencies.py verify",
        "python3 scripts/package_trino_bun_dependencies.py create",
        "python3 scripts/package_trino_bun_dependencies.py verify",
        "python3 scripts/prepare_trino_bun_input.py download",
        "python3 scripts/prepare_trino_bun_input.py stage",
        EXPECTED_BUN_INPUT["url"],
        EXPECTED_BUN_INPUT["sha256"],
        EXPECTED_ARTIFACT_TYPE,
        EXPECTED_DESCRIPTOR_MEDIA_TYPE,
        EXPECTED_BUN_DESCRIPTOR_MEDIA_TYPE,
        EXPECTED_BUN_ARCHIVE_MEDIA_TYPE,
        "oras push",
        "cosign sign",
        "cosign attest-blob",
        "cosign attach attestation",
        "cosign verify-attestation",
        "anonymous-pull-signature-bundle.json",
        "cosign-verify-anonymous-pull.json",
        "--type slsaprovenance1",
        '"https://slsa.dev/provenance/v1"',
        '"https://in-toto.io/Statement/v1"',
        "verified SLSA v1 payload does not uniquely bind",
        "predicate.buildDefinition.resolvedDependencies",
        '"file:trivy-version.json"',
        "trivy-vulnerability.json",
        "trino-maven-dependencies-483.cdx.json",
        "trivy-bun-vulnerability.json",
        "trivy-bun-vulnerability-raw.json",
        VEX_PATH.as_posix(),
        "trino-bun-dependencies-483.cdx.json",
    )
    for value in required:
        if value not in workflow:
            _fail("WORKFLOW_REQUIRED", value)
    if (
        lines.count("          scan-type: rootfs") != 1
        or lines.count("          scan-type: sbom") != 1
        or lines.count(
            "          scan-ref: ${{ runner.temp }}/maven-repository-a"
        )
        != 1
        or workflow.count(
            '            --repository "${RUNNER_TEMP}/maven-repository-a" \\\n'
        )
        != 1
    ):
        _fail(
            "WORKFLOW_MAVEN_SCAN",
            (
                "Maven discovery must use the exact repository rootfs and "
                "the scan must consume its closed SBOM"
            ),
        )
    if (
        workflow.count(EXPECTED_MAVEN_SCAN_REPORT_BLOCK) != 1
        or workflow.count(EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK) != 1
        or workflow.count(EXPECTED_CANDIDATE_HIDDEN_UPLOAD_BLOCK) != 1
        or workflow.count("        id: verify_maven_scan") != 1
    ):
        _fail(
            "WORKFLOW_MAVEN_DIAGNOSTICS",
            (
                "the report-only Maven scan must feed the explicit blocking "
                "verifier, and both exact hidden candidate inventories must "
                "be explicitly retained"
            ),
        )
    if (
        workflow.count(EXPECTED_BUN_SCAN_STAGE_BLOCK) != 1
        or workflow.count(EXPECTED_BUN_ADJUSTED_SCAN_REPORT_BLOCK) != 1
        or workflow.count(EXPECTED_BUN_SCAN_GATE_BLOCK) != 1
        or workflow.count(EXPECTED_BUN_FAILURE_DIAGNOSTIC_QUARTET_BLOCK) != 1
        or workflow.count(EXPECTED_BUN_FAILURE_DIAGNOSTIC_BLOCK) != 1
        or workflow.count("        id: stage_bun_scan_input") != 1
        or workflow.count("        id: verify_bun_scan") != 1
        or workflow.count("        id: verify_bun_diagnostic_quartet") != 1
        or workflow.count(EXPECTED_BUN_DIAGNOSTIC_ARTIFACT_PREFIX) != 1
        or lines.count("          include-hidden-files: true") != 3
    ):
        _fail(
            "WORKFLOW_BUN_DIAGNOSTICS",
            (
                "the adjusted Bun scan must remain report-only, retain the "
                "exact reviewed OpenVEX before the explicit blocking verifier, "
                "fail closed unless every diagnostic file exists and is "
                "nonempty, and retain only the exact run-scoped failure "
                "diagnostic quartet"
            ),
        )
    publish_workflow = workflow.split("\n  publish:\n", 1)
    if (
        len(publish_workflow) != 2
        or workflow.count(EXPECTED_CANDIDATE_DOWNLOAD_BLOCK) != 1
        or EXPECTED_BUN_DIAGNOSTIC_ARTIFACT_PREFIX in publish_workflow[1]
    ):
        _fail(
            "WORKFLOW_BUN_DIAGNOSTIC_ISOLATION",
            (
                "the failure diagnostic artifact must never become a candidate, "
                "admission, or publication input"
            ),
        )
    if (
        workflow.count(EXPECTED_ORAS_PUSH_BLOCK) != 1
        or "--disable-path-validation" in workflow
        or '"${candidate}/maven-dependency-manifest.json:' in workflow
        or '"${candidate}/trino-maven-dependencies-483.tar.gz:' in workflow
        or '"${candidate}/bun-dependency-manifest.json:' in workflow
        or '"${candidate}/trino-bun-dependencies-483.tar.gz:' in workflow
    ):
        _fail(
            "WORKFLOW_ORAS_PATHS",
            (
                "ORAS publication must use reviewed basenames from the "
                "validated candidate directory without disabling path validation"
            ),
        )
    if workflow.count(EXPECTED_ORAS_DIGEST_VALIDATION_BLOCK) != 1:
        _fail(
            "WORKFLOW_ORAS_DIGEST",
            "ORAS publication must require one exact lowercase sha256 digest",
        )
    if (
        workflow.count(EXPECTED_SLSA_STATEMENT_ATTESTATION_BLOCK) != 1
        or workflow.count(EXPECTED_PARQUET_SLSA_RESOLVED_DEPENDENCY_BLOCK)
        != 1
        or 'cosign attest --yes \\\n' in workflow
        or '--predicate "${candidate}/slsa-provenance.json"' in workflow
    ):
        _fail(
            "WORKFLOW_SLSA_STATEMENT",
            (
                "SLSA publication must sign and attach the exact repository-"
                "generated in-toto Statement/v1"
            ),
        )
    if (
        workflow.count(EXPECTED_SETTINGS_MOUNT) != 5
        or workflow.count(f"{EXPECTED_SETTINGS_ARGUMENT} \\\n") != 5
        or lines.count(f"            {EXPECTED_SETTINGS_MOUNT} \\") != 4
        or lines.count(f"              {EXPECTED_SETTINGS_MOUNT} \\") != 1
    ):
        _fail(
            "WORKFLOW_SETTINGS",
            (
                "both online resolvers and the two-run network-none rebuild "
                "must use the exact read-only repository settings"
            ),
        )
    staging_commands = re.findall(
        r"python3 scripts/"
        r"(prepare_trino_bun_input\.py stage|"
        r"remediate_parquet_jackson\.py stage-artifact)",
        workflow,
    )
    if (
        workflow.count(
            "python3 scripts/remediate_parquet_jackson.py prepare-source"
        )
        != 1
        or workflow.count(
            "python3 scripts/remediate_parquet_jackson.py stage-artifact"
        )
        != 2
        or workflow.count(
            "python3 scripts/remediate_parquet_jackson.py seal-artifact"
        )
        != 2
        or workflow.count(
            "python3 scripts/remediate_parquet_jackson.py compare-artifacts"
        )
        != 1
        or workflow.count(
            '-Dproject.build.outputTimestamp="${PARQUET_OUTPUT_TIMESTAMP}"'
        )
        != 2
        or workflow.count(
            "              --ignore-transitive-repositories \\\n"
        )
        != 4
        or workflow.count('--volume "${parquet_source}:/workspace" \\') != 2
        or workflow.count(
            '--volume "${parquet_repository}:/m2" \\'
        )
        != 2
        or workflow.count(
            '"${RUNNER_TEMP}/parquet-source-${suffix}"'
        )
        != 1
        or "suffixes+=(b)" not in workflow
        or staging_commands
        != [
            "prepare_trino_bun_input.py stage",
            "remediate_parquet_jackson.py stage-artifact",
            "prepare_trino_bun_input.py stage",
            "remediate_parquet_jackson.py stage-artifact",
        ]
    ):
        _fail(
            "WORKFLOW_SOURCE_REMEDIATION",
            (
                "the exact Parquet source must be independently fetched, "
                "built twice, compared, staged after the Bun input, and sealed"
            ),
        )
    if (
        workflow.count(
            "python3 scripts/package_trino_maven_dependencies.py \\\n"
            '            prune-reactor-outputs --repository "${repository}"'
        )
        != 2
        or 'rm -rf "${repository}/io/trino"' in workflow
    ):
        _fail(
            "WORKFLOW_REACTOR_PRUNE",
            "each fresh repository must use the bounded reactor-output pruner",
        )
    if (
        workflow.count(
            "python3 scripts/verify_trino_maven_feasibility.py verify-candidate"
        )
        != 4
        or workflow.count(
            "python3 scripts/verify_trino_maven_feasibility.py "
            "prune-vulnerable-inputs"
        )
        != 2
    ):
        _fail(
            "WORKFLOW_REMEDIATION_BOUNDARY",
            (
                "each Trino build must revalidate source postimages and each "
                "online repository must apply the hardened metadata pruning"
            ),
        )
    for forbidden in (
        "./mvnw",
        "maven-wrapper.jar",
        "trinodb/trino:483",
        "trino-server-483.tar.gz\" --output",
        "--privileged",
        "setup-qemu",
        "binfmt --install",
        ":latest",
        "--workdir /workspace",
    ):
        if forbidden in workflow:
            _fail("WORKFLOW_FORBIDDEN", forbidden)
    offline_rebuild = contract.get("offline_rebuild")
    if not isinstance(offline_rebuild, dict):
        _fail("WORKFLOW_OFFLINE_COMMAND", "contract offline rebuild is missing")
    expected_offline_command = offline_rebuild.get("command")
    if not isinstance(expected_offline_command, str):
        _fail("WORKFLOW_OFFLINE_COMMAND", "contract command is missing")
    if (
        not _matches_exact_json(
            offline_rebuild.get("repository_settings"),
            EXPECTED_OFFLINE_REPOSITORY_SETTINGS,
        )
        or not _matches_exact_json(
            offline_rebuild.get("bun_cache"),
            EXPECTED_OFFLINE_BUN_CACHE,
        )
        or not _matches_exact_json(
            offline_rebuild.get("compiler_debug_information"),
            EXPECTED_OFFLINE_COMPILER_DEBUG,
        )
    ):
        _fail(
            "WORKFLOW_SETTINGS",
            "contract offline build settings differ",
        )
    if not _matches_exact_json(
        offline_rebuild.get("maven_repository"),
        EXPECTED_OFFLINE_MAVEN_REPOSITORY,
    ):
        _fail(
            "WORKFLOW_OFFLINE_REPOSITORY",
            "contract offline Maven repository boundary differs",
        )
    observed_offline_command = _offline_maven_command(workflow)
    if observed_offline_command != expected_offline_command:
        _fail(
            "WORKFLOW_OFFLINE_COMMAND",
            (
                f"expected {expected_offline_command!r}, "
                f"found {observed_offline_command!r}"
            ),
        )
    observed_resolution_commands = _resolution_maven_commands(workflow)
    if observed_resolution_commands != (
        EXPECTED_RESOLUTION_COMMAND,
        EXPECTED_RESOLUTION_COMMAND,
    ):
        _fail(
            "WORKFLOW_RESOLUTION_COMMAND",
            f"resolver commands differ: {observed_resolution_commands!r}",
        )
    if (
        workflow.count(EXPECTED_OFFLINE_MAVEN_REPOSITORY_ASSIGNMENT) != 1
        or workflow.count(EXPECTED_OFFLINE_MAVEN_REPOSITORY_EXTRACTION) != 1
        or workflow.count(EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT) != 1
        or workflow.count(
            'test ! -e "${offline_repository}"'
        )
        != 1
        or workflow.count(EXPECTED_OFFLINE_RESOLVER_LOCK_ARGUMENT) != 2
        or workflow.count(EXPECTED_OFFLINE_LIFECYCLE) != 2
        or "-Dmaven.install.skip" in workflow
        or '${offline_source}/.m2' in workflow
        or '--volume "${offline_repository}:/m2"' in workflow
    ):
        _fail(
            "WORKFLOW_OFFLINE_REPOSITORY",
            (
                "each offline build must extract one fresh Maven repository "
                "outside the source checkout, mount it read-only at /m2, and "
                "stop at package without local-repository writes"
            ),
        )
    if (
        workflow.count("--network none") != 1
        or "for suffix in a b; do" not in workflow
        or '"fresh_source_checkouts": 2' not in workflow
        or '"fresh_snapshot_extractions": 2' not in workflow
        or workflow.count('"compiler_debug_level": "source,lines"') != 1
        or workflow.count('"local_variable_debug_table_permitted": False') != 1
        or workflow.count("-Dmaven.compiler.debuglevel=source,lines") != 2
        or workflow.count(
            'verify-server-distribution --archive "${output}"'
        )
        != 1
        or workflow.count(EXPECTED_OFFLINE_DIGEST_COMMAND) != 1
        or 'sha256sum "${output}" >' in workflow
    ):
        _fail(
            "WORKFLOW_OFFLINE",
            (
                "two network-none rebuilds, exact compiler evidence, and "
                "filename-independent digest comparison are required"
            ),
        )
    if (
        lines.count("  BUN_CACHE_DIRECTORY: /bun-cache") != 1
        or lines.count("  BUN_REGISTRY: https://registry.npmjs.org/") != 1
        or workflow.count('--env CI=true \\') != 5
        or workflow.count(
            '--env BUN_INSTALL_CACHE_DIR="${BUN_CACHE_DIRECTORY}" \\'
        )
        != 3
        or workflow.count('--env BUN_CONFIG_REGISTRY="${BUN_REGISTRY}" \\')
        != 3
        or workflow.count(
            '--volume "${bun_cache}:${BUN_CACHE_DIRECTORY}" \\'
        )
        != 2
        or workflow.count(
            '--volume "${offline_bun_cache}:${BUN_CACHE_DIRECTORY}:ro" \\'
        )
        != 1
        or '${offline_source}/.bun-cache' in workflow
        or workflow.count(
            'offline_bun_cache="${RUNNER_TEMP}/'
            'trino-offline-bun-cache-${suffix}"'
        )
        != 1
        or workflow.count(
            "python3 scripts/package_trino_bun_dependencies.py create"
        )
        != 2
        or workflow.count(
            "python3 scripts/package_trino_bun_dependencies.py verify \\"
        )
        != 3
        or workflow.count(
            "python3 scripts/package_trino_bun_dependencies.py verify-cache \\"
        )
        != 1
        or workflow.count(
            "python3 scripts/verify_trino_dependency_publisher.py \\\n"
            "            verify-bun-snapshot \\"
        )
        != 2
        or '"fresh_bun_cache_extractions": 2' not in workflow
        or '"bun_lockfile_mode": "frozen-via-CI-profile"' not in workflow
    ):
        _fail(
            "WORKFLOW_BUN_CACHE",
            "Bun cache must be frozen, independently reconstructed, and read-only offline",
        )
    if (
        workflow.count(EXPECTED_BUN_ADJUSTED_SCAN_REPORT_BLOCK) != 1
        or workflow.count(EXPECTED_RECORD_TRIVY_CACHE_BLOCK) != 1
        or workflow.count(
            "TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy"
        )
        != 2
    ):
        _fail(
            "WORKFLOW_TRIVY_CACHE",
            (
                "direct OpenVEX and version metadata commands must reuse the "
                "Trivy action vulnerability database cache"
            ),
        )
    if (
        workflow.count(
            "python3 scripts/verify_trino_dependency_publisher.py \\\n"
            "            stage-bun-scan-input"
        )
        != 1
        or workflow.count(
            "python3 scripts/verify_trino_dependency_publisher.py \\\n"
            "            verify-bun-scan"
        )
        != 1
        or workflow.count("apply-source-overlay \\") != 3
        or workflow.count(
            "scan-ref: ${{ runner.temp }}/trino-bun-scan-input"
        )
        != 2
        or "scan-ref: ${{ runner.temp }}/bun-cache-a" in workflow
        or workflow.count('TRIVY_INCLUDE_DEV_DEPS: "true"') != 3
        or workflow.count("list-all-pkgs: true") != 3
        or workflow.count('--vex "${vex}"') != 1
        or workflow.count("--skip-db-update") != 1
        or "--raw-report \\" not in workflow
        or "--adjusted-report \\" not in workflow
    ):
        _fail(
            "WORKFLOW_BUN_SCAN",
            (
                "both exact Bun lockfiles and development dependencies must "
                "be analyzed with package-presence verification"
            ),
        )
    if (
        workflow.count(EXPECTED_BUN_STAGE_BLOCK)
        != EXPECTED_BUN_INPUT["independent_downloads"]
        or workflow.count('bun_archive="${RUNNER_TEMP}/bun-linux-aarch64-a.zip"')
        != 1
        or workflow.count('bun_archive="${RUNNER_TEMP}/bun-linux-aarch64-b.zip"')
        != 1
        or workflow.count('"${BUN_URL}"')
        != EXPECTED_BUN_INPUT["independent_downloads"] + 1
        or lines.count(f'  BUN_URL: {EXPECTED_BUN_INPUT["url"]}') != 1
        or lines.count(
            f'  BUN_ARCHIVE_SHA256: {EXPECTED_BUN_INPUT["sha256"]}'
        )
        != 1
        or lines.count(f'  BUN_ARCHIVE_SIZE: "{EXPECTED_BUN_INPUT["size"]}"')
        != 1
    ):
        _fail(
            "WORKFLOW_BUN_INPUT",
            "two publisher inputs plus one PR build input are required",
        )
    publication = contract.get("publication", {})
    if (
        publication.get("permitted") is not False
        or publication.get("workflow_present") is not True
        or publication.get("workflow") != WORKFLOW_PATH.as_posix()
        or publication.get("allowed_ref") != "refs/heads/main"
        or publication.get("artifact_role") != "review_pending_dependency_evidence"
        or publication.get("retire_in_evidence_review_pr") is not True
        or publication.get("pull_request_behavior")
        != "static_read_only_contract_validation"
        or not _matches_exact_json(
            publication.get("pending_review_repair"),
            EXPECTED_PENDING_REVIEW_REPAIR,
        )
        or not _matches_exact_json(
            publication.get("evidence_review_inventory_policy"),
            {
                "recursive_closed_world_required": True,
                "regular_files_only": True,
                "directories_and_symlinks_rejected": True,
            },
        )
    ):
        _fail("PUBLICATION", "authorized publication lifecycle differs")
    failed = contract.get("failed_publications")
    if (
        not isinstance(failed, list)
        or len(failed) != 1
        or failed[0].get("run_id") != "30231656483"
        or failed[0].get("run_attempt") != "1"
        or failed[0].get("source_sha")
        != "1ae1996eaf654e69daad60c574c7abb4e4d2be3b"
        or failed[0].get("reference")
        != (
            "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@"
            "sha256:0394143034298f4c6606c288e8ef97154826978bf3aa97"
            "e1e952499f8af5075c"
        )
        or failed[0].get("admitted") is not False
        or len(failed[0].get("reasons", [])) != 5
    ):
        _fail("PUBLICATION", "failed publication record differs")


def _validate_maven_policy_inventory(root: Path) -> None:
    directory = root / JVM_CONFIG_PATH.parent
    try:
        metadata = directory.lstat()
    except OSError as error:
        _fail("POLICY_FILE", f"{directory}: {error}")
    if not stat.S_ISDIR(metadata.st_mode):
        _fail("POLICY_FILE", f"{directory} must be one real directory")
    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        _fail("POLICY_FILE", f"{directory}: {error}")
    if [entry.name for entry in entries] != [JVM_CONFIG_PATH.name]:
        _fail(
            "POLICY_FILE",
            "Maven policy directory must contain only jvm.config",
        )
    _read_reviewed_regular_file(entries[0], code="POLICY_FILE")


def _validate_policy_hashes(root: Path, contract: Mapping[str, Any]) -> None:
    _validate_maven_policy_inventory(root)
    expected_paths = {
        SETTINGS_PATH,
        JVM_CONFIG_PATH,
        PACKAGER_PATH,
        BUN_PACKAGER_PATH,
        BUN_PREPARER_PATH,
        PARQUET_REMEDIATION_PATH,
        VERIFIER_PATH,
        TEST_PATH,
        BUN_TEST_PATH,
        PARQUET_REMEDIATION_TEST_PATH,
        SOURCE_OVERLAY_PATH,
        DISTRIBUTION_REMEDIATION_PATH,
        BUILD_PLUGIN_REMEDIATION_PATH,
        VEX_PATH,
        OVERLAY_ADR_PATH,
        PARQUET_REMEDIATION_ADR_PATH,
        DISTRIBUTION_REMEDIATION_ADR_PATH,
        BLOCKER_ADR_PATH,
        BUILD_PLUGIN_REMEDIATION_ADR_PATH,
        AUTHORIZED_FEASIBILITY_RECORD_PATH,
        AUTHORIZED_FEASIBILITY_RECEIPT_PATH,
        BLOCKER_CLASSIFICATION_PATH,
        BLOCKER_HARDENED_SCM_POM_PATH,
        BLOCKER_HARDENED_SCM_MANAGER_POM_PATH,
    }
    policy_files = contract.get("policy_files")
    if not isinstance(policy_files, list):
        _fail("POLICY_FILE", "policy_files must be a list")
    observed: set[Path] = set()
    for record in policy_files:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            _fail("POLICY_FILE", "policy file records are closed-world")
        path = Path(record["path"])
        if path in observed or path not in expected_paths:
            _fail("POLICY_FILE", f"unexpected or duplicate policy file: {path}")
        observed.add(path)
        if record["sha256"] != _sha256(root / path):
            _fail("POLICY_FILE", f"hash differs: {path}")
    if observed != expected_paths:
        _fail("POLICY_FILE", f"policy file set differs: {observed!r}")


def audit(root: Path) -> None:
    contract = _load_json(root / CONTRACT_PATH)
    admission = _load_json(root / ADMISSION_PATH)
    _validate_authorization(contract, at=None)
    _validate_source_overlay_contract(root, contract, at=None)
    _validate_source_remediation_contract(contract, at=None)
    _validate_distribution_remediation_contract(root, contract, at=None)
    _validate_build_plugin_remediation_contract(root, contract, at=None)
    _validate_blocker_evidence(root)
    lifecycle = contract.get("lifecycle", {})
    if not _matches_exact_json(
        lifecycle,
        {
            "state": "dependency_snapshot_publication_reauthorization_pending",
            "contract_only": False,
            "dependency_artifact_present": False,
            "publication_workflow_permitted": False,
            "image_publication_permitted": False,
            "resident_admission_permitted": False,
            "runtime_reconciliation_permitted": False,
        },
    ):
        _fail("LIFECYCLE", f"unexpected lifecycle: {lifecycle!r}")
    source = contract.get("source", {})
    if (
        source.get("repository") != EXPECTED_SOURCE_REPOSITORY
        or source.get("release_tag") != EXPECTED_TAG
        or source.get("commit_sha") != EXPECTED_COMMIT
        or source.get("tree_sha") != EXPECTED_TREE
        or source.get("unmodified_source_required") is not False
        or source.get("pristine_source_required_before_overlay") is not True
    ):
        _fail("SOURCE", "exact Trino source binding differs")
    if not _matches_exact_json(
        contract.get("toolchain", {}).get("builder", {}).get("index"),
        EXPECTED_BUILDER,
    ):
        _fail("BUILDER", "builder index differs")
    dependency_resolution = contract.get("dependency_resolution", {})
    reactor_outputs = dependency_resolution.get("reactor_outputs", {})
    if (
        not _matches_exact_json(
            dependency_resolution.get("repositories"),
            list(EXPECTED_REPOSITORIES.values()),
        )
        or dependency_resolution.get("transitive_dependency_repositories_ignored")
        is not True
        or not _matches_exact_json(
            dependency_resolution.get("repository_mirrors"),
            [
                {
                    "id": values[0][1],
                    "mirror_of": values[1][1],
                    "url": values[3][1],
                }
                for values in EXPECTED_REPOSITORY_MIRRORS
            ],
        )
        or not _matches_exact_json(
            dependency_resolution.get("settings_policy"),
            EXPECTED_SETTINGS_POLICY,
        )
        or not _matches_exact_json(
            dependency_resolution.get("external_inputs"),
            [
                EXPECTED_BUN_INPUT,
                EXPECTED_PARQUET_SOURCE_REMEDIATION,
                EXPECTED_SCM_METADATA_REMEDIATION,
            ],
        )
        or not _matches_exact_json(
            dependency_resolution.get("bun_package_cache"),
            EXPECTED_BUN_PACKAGE_CACHE,
        )
        or reactor_outputs.get("repository_path_prefix") != "io/trino/"
        or reactor_outputs.get("dependency_input_permitted") is not False
        or reactor_outputs.get("rebuild_from_reviewed_source_required") is not True
        or not _matches_exact_json(
            reactor_outputs.get("exact_external_build_extension"),
            EXPECTED_TRINO_BUILD_EXTENSION,
        )
        or not _matches_exact_json(
            reactor_outputs.get("exact_external_maven_inputs"),
            EXPECTED_TRINO_EXTERNAL_MAVEN_INPUTS,
        )
    ):
        _fail("REPOSITORIES", "contract repository allowlist differs")
    snapshot = contract.get("snapshot", {})
    if (
        snapshot.get("artifact_type") != EXPECTED_ARTIFACT_TYPE
        or snapshot.get("descriptor_media_type")
        != EXPECTED_DESCRIPTOR_MEDIA_TYPE
        or not _matches_exact_json(
            snapshot.get("manifest", {}).get("schema_version"),
            2,
        )
        or not _matches_exact_json(
            snapshot.get("trivy_rootfs_omission_contract"),
            {
                "schema_version": 1,
                "unknown_omissions_permitted": False,
                "reviewed_omissions": EXPECTED_TRIVY_ROOTFS_OMISSIONS,
            },
        )
        or not _matches_exact_json(
            snapshot.get("bun_cache"),
            {
                "descriptor_media_type": EXPECTED_BUN_DESCRIPTOR_MEDIA_TYPE,
                "archive_media_type": EXPECTED_BUN_ARCHIVE_MEDIA_TYPE,
                "manifest_schema_version": 1,
            },
        )
    ):
        _fail("SNAPSHOT_FORMAT", "dependency snapshot v2 contract differs")
    if not _matches_exact_json(
        snapshot.get("visibility_bootstrap"),
        {
            "required_visibility": "public",
            "sign_and_attest_before_anonymous_pull": True,
            "owner_action_on_first_private_run": "set-package-public-and-rerun",
            "failed_attempt_admitted": False,
            "user_credential_fallback": False,
        },
    ):
        _fail("VISIBILITY", "first-publication visibility contract differs")
    provenance = snapshot.get("authentication", {}).get("provenance", {})
    if not _matches_exact_json(
        provenance.get("parquet_source_remediation_resolved_dependency"),
        EXPECTED_PARQUET_SLSA_RESOLVED_DEPENDENCY,
    ):
        _fail(
            "SLSA_SOURCE_REMEDIATION",
            "Parquet source remediation provenance binding differs",
        )
    repository_state = admission.get("repository_state", {})
    if (
        not _matches_exact_json(
            admission.get("source_overlay_authorization"),
            EXPECTED_ADMISSION_OVERLAY_AUTHORIZATION,
        )
        or not _matches_exact_json(
            admission.get("source_remediation_authorization"),
            EXPECTED_ADMISSION_SOURCE_REMEDIATION_AUTHORIZATION,
        )
        or not _matches_exact_json(
            admission.get("distribution_remediation_authorization"),
            EXPECTED_ADMISSION_DISTRIBUTION_REMEDIATION_AUTHORIZATION,
        )
        or not _matches_exact_json(
            admission.get("build_plugin_remediation_authorization"),
            EXPECTED_ADMISSION_BUILD_PLUGIN_REMEDIATION_AUTHORIZATION,
        )
        or not _matches_exact_json(
            admission.get("dependency_snapshot_publication_reauthorization"),
            EXPECTED_PUBLICATION_REAUTHORIZATION,
        )
        or not _matches_exact_json(
            admission.get("independent_review"),
            EXPECTED_INDEPENDENT_REVIEW,
        )
        or not _matches_exact_json(
            admission.get("owner_only_approval_exception"),
            EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION,
        )
        or not _matches_exact_json(
            admission.get("pending_publication_repair"),
            EXPECTED_PENDING_REVIEW_REPAIR,
        )
        or repository_state.get("publication_workflow_permitted") is not False
        or repository_state.get("dependency_artifact_present") is not False
        or repository_state.get("resident_ledger_permitted") is not False
        or repository_state.get("runtime_manifests_permitted") is not False
    ):
        _fail("ADMISSION", "admission state crosses the publisher boundary")
    _validate_settings(root)
    _validate_policy_hashes(root, contract)
    try:
        workflow = (root / WORKFLOW_PATH).read_text(encoding="utf-8")
    except OSError as error:
        _fail("WORKFLOW", str(error))
    _validate_workflow(contract, workflow)


def publication_status(contract: dict[str, Any], admission: dict[str, Any]) -> str:
    permissions = [
        contract.get("lifecycle", {}).get("publication_workflow_permitted"),
        contract.get("publication", {}).get("permitted"),
        admission.get("repository_state", {}).get(
            "publication_workflow_permitted"
        ),
    ]
    if _matches_exact_json(permissions, [True, True, True]):
        return "active"
    if _matches_exact_json(permissions, [False, False, False]):
        return "blocked"
    _fail("LIFECYCLE", "publication permission records disagree")


def audit_source(root: Path, checkout: Path) -> None:
    contract = _load_json(root / CONTRACT_PATH)
    _validate_authorization(contract, at=dt.datetime.now(dt.timezone.utc))
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tag_object = subprocess.run(
            ["git", "rev-parse", f"refs/tags/{EXPECTED_TAG}"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tag_commit = subprocess.run(
            ["git", "rev-parse", f"refs/tags/{EXPECTED_TAG}^{{}}"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        _fail("SOURCE_GIT", str(error))
    if (
        commit != EXPECTED_COMMIT
        or tree != EXPECTED_TREE
        or remote != EXPECTED_SOURCE_REPOSITORY
        or tag_object != EXPECTED_TAG_OBJECT
        or tag_commit != EXPECTED_COMMIT
        or status
    ):
        _fail(
            "SOURCE_GIT",
            (
                f"commit={commit}, tree={tree}, remote={remote}, "
                f"tag={tag_object}, tag_commit={tag_commit}, dirty={bool(status)}"
            ),
        )
    for record in contract["source"]["preimages"]:
        path = checkout / record["path"]
        if _sha256(path) != record["sha256"]:
            _fail("SOURCE_PREIMAGE", record["path"])
    allowed = set(EXPECTED_REPOSITORIES.values())
    for pom in checkout.rglob("pom.xml"):
        try:
            xml = ET.parse(pom)
        except ET.ParseError as error:
            _fail("SOURCE_POM", f"{pom}: {error}")
        for element in xml.getroot().iter():
            local = element.tag.rsplit("}", 1)[-1]
            if local not in {"repository", "pluginRepository"}:
                continue
            urls = [
                child.text.strip()
                for child in element
                if child.tag.rsplit("}", 1)[-1] == "url" and child.text
            ]
            if len(urls) != 1 or urls[0] not in allowed:
                _fail("SOURCE_REPOSITORY", f"{pom}: {urls!r}")


def audit_transfer_log(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except OSError as error:
        _fail("TRANSFER_LOG", str(error))
    allowed = tuple(EXPECTED_REPOSITORIES.values())
    observed = 0
    for raw_line in text.splitlines():
        line = ANSI_ESCAPE_RE.sub("", raw_line)
        if not MAVEN_TRANSFER_EVENT_PREFIX_RE.match(line):
            continue
        event = MAVEN_TRANSFER_EVENT_RE.match(line)
        if event is None:
            _fail("TRANSFER_LOG", f"malformed Maven transfer event: {line}")
        url = event.group("url")
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            _fail("TRANSFER_LOG", f"unsafe Maven transfer URL: {url}")
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if not any(normalized.startswith(prefix) for prefix in allowed):
            _fail("TRANSFER_LOG", f"non-allowlisted Maven transfer: {url}")
        observed += 1
        observed += 1
    if observed == 0:
        _fail("TRANSFER_LOG", "no Maven repository transfers were observed")


def verify_server_distribution(path: Path) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as error:
        _fail("SERVER_DISTRIBUTION", str(error))
    names = [member.name for member in members]
    name_set = set(names)
    if len(names) != len(name_set):
        _fail("SERVER_DISTRIBUTION", "duplicate archive path")
    members_by_name = {member.name: member for member in members}
    distribution_prefix = f"{EXPECTED_SERVER_DISTRIBUTION_ROOT}/"
    for member in members:
        parts = member.name.split("/")
        if (
            member.name == EXPECTED_SERVER_DISTRIBUTION_ROOT
            and member.isdir()
        ):
            continue
        if (
            not member.name.startswith(distribution_prefix)
            or member.name.startswith("/")
            or "\\" in member.name
            or any(part in {"", ".", ".."} for part in parts)
            or not (member.isdir() or member.isfile() or member.islnk())
        ):
            _fail(
                "SERVER_DISTRIBUTION",
                f"unsafe or unexpected member: {member.name}",
            )
        if parts[1] not in EXPECTED_SERVER_DISTRIBUTION_ROOTS:
            if (
                member.name not in EXPECTED_SERVER_DISTRIBUTION_EMPTY_DIRECTORIES
                or not member.isdir()
            ):
                _fail(
                    "SERVER_DISTRIBUTION",
                    f"distribution root differs: {parts[1]!r}",
                )
        if member.islnk() and (
            member.linkname not in name_set
            or not member.linkname.startswith(distribution_prefix)
        ):
            _fail(
                "SERVER_DISTRIBUTION",
                f"hard link escapes archive: {member.name}",
            )

    def resolves_to_regular_file(name: str) -> bool:
        visited: set[str] = set()
        while True:
            if name in visited:
                _fail("SERVER_DISTRIBUTION", f"hard link cycle: {name}")
            visited.add(name)
            member = members_by_name[name]
            if member.isfile():
                return True
            if not member.islnk():
                return False
            name = member.linkname

    for member in members:
        if member.islnk() and not resolves_to_regular_file(member.name):
            _fail(
                "SERVER_DISTRIBUTION",
                f"hard link target is not a regular file: {member.name}",
            )
    plugins = {
        parts[2]
        for name in names
        if len(parts := name.split("/")) > 2 and parts[1] == "plugin"
    }
    if plugins != {"iceberg"}:
        _fail(
            "SERVER_DISTRIBUTION",
            f"plugin set differs: {sorted(plugins)!r}",
        )
    missing_directories = (
        EXPECTED_SERVER_DISTRIBUTION_EMPTY_DIRECTORIES - name_set
    )
    if missing_directories:
        _fail(
            "SERVER_DISTRIBUTION",
            (
                "required empty directories are missing: "
                f"{sorted(missing_directories)!r}"
            ),
        )
    missing = EXPECTED_SERVER_DISTRIBUTION_FILES - name_set
    if missing:
        _fail(
            "SERVER_DISTRIBUTION",
            f"required members are missing: {sorted(missing)!r}",
        )
    invalid = {
        name
        for name in EXPECTED_SERVER_DISTRIBUTION_FILES
        if not resolves_to_regular_file(name)
    }
    if invalid:
        _fail(
            "SERVER_DISTRIBUTION",
            (
                "required members are not regular files or validated hard "
                f"links: {sorted(invalid)!r}"
            ),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--root", type=Path, default=Path("."))
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--root", type=Path, default=Path("."))
    authorize.add_argument("--at")
    authorize_use_parser = commands.add_parser("authorize-use")
    authorize_use_parser.add_argument("--root", type=Path, default=Path("."))
    authorize_use_parser.add_argument("--validation-point", required=True)
    authorize_use_parser.add_argument("--at")
    independent_review = commands.add_parser("verify-independent-review")
    independent_review.add_argument("--root", type=Path, default=Path("."))
    independent_review.add_argument("--repository", required=True)
    independent_review.add_argument("--commit", required=True)
    publication_status = commands.add_parser("publication-status")
    publication_status.add_argument("--root", type=Path, default=Path("."))
    source = commands.add_parser("audit-source")
    source.add_argument("--root", type=Path, default=Path("."))
    source.add_argument("--checkout", type=Path, required=True)
    source_overlay = commands.add_parser("apply-source-overlay")
    source_overlay.add_argument("--root", type=Path, default=Path("."))
    source_overlay.add_argument("--checkout", type=Path, required=True)
    builder_settings = commands.add_parser("audit-builder-settings")
    builder_settings.add_argument("--settings", type=Path, required=True)
    transfer = commands.add_parser("audit-transfer-log")
    transfer.add_argument("--log", type=Path, required=True)
    distribution = commands.add_parser("verify-server-distribution")
    distribution.add_argument("--archive", type=Path, required=True)
    bun_scan_input = commands.add_parser("stage-bun-scan-input")
    bun_scan_input.add_argument("--checkout", type=Path, required=True)
    bun_scan_input.add_argument("--output", type=Path, required=True)
    bun_scan = commands.add_parser("verify-bun-scan")
    bun_scan.add_argument("--root", type=Path, default=Path("."))
    bun_scan.add_argument("--scan-input", type=Path, required=True)
    bun_scan.add_argument("--raw-report", type=Path, required=True)
    bun_scan.add_argument("--adjusted-report", type=Path, required=True)
    bun_snapshot = commands.add_parser("verify-bun-snapshot")
    bun_snapshot.add_argument("--descriptor", type=Path, required=True)
    bun_snapshot.add_argument("--archive", type=Path, required=True)
    maven_scan = commands.add_parser("verify-maven-scan")
    maven_scan.add_argument("--descriptor", type=Path, required=True)
    maven_scan.add_argument("--sbom", type=Path, required=True)
    maven_scan.add_argument("--report", type=Path, required=True)
    maven_sbom = commands.add_parser("generate-maven-sbom")
    maven_sbom.add_argument("--descriptor", type=Path, required=True)
    maven_sbom.add_argument("--repository", type=Path, required=True)
    maven_sbom.add_argument("--rootfs-sbom", type=Path, required=True)
    maven_sbom.add_argument("--output", type=Path, required=True)
    bind = commands.add_parser("bind-artifact-evidence")
    bind.add_argument("--reference", required=True)
    bind.add_argument("--descriptor", type=Path, required=True)
    bind.add_argument("--maven-sbom", type=Path, required=True)
    bind.add_argument("--maven-report", type=Path, required=True)
    bind.add_argument("--bun-sbom", type=Path, required=True)
    bind.add_argument("--bun-raw-report", type=Path, required=True)
    bind.add_argument("--bun-adjusted-report", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "audit":
            audit(args.root.resolve())
        elif args.command == "authorize":
            contract = _load_json(args.root.resolve() / CONTRACT_PATH)
            instant = (
                _parse_time(args.at)
                if args.at
                else dt.datetime.now(dt.timezone.utc)
            )
            _validate_authorization(contract, at=instant)
            _validate_source_overlay_contract(
                args.root.resolve(),
                contract,
                at=instant,
            )
            _validate_source_remediation_contract(contract, at=instant)
            _validate_distribution_remediation_contract(
                args.root.resolve(),
                contract,
                at=instant,
            )
            if contract.get("lifecycle", {}).get("state") != (
                "dependency_snapshot_publication_pending"
            ):
                _fail("LIFECYCLE", "publisher is retired or not approved")
            if (
                contract.get("lifecycle", {}).get(
                    "publication_workflow_permitted"
                )
                is not True
                or contract.get("publication", {}).get("permitted") is not True
            ):
                _fail("LIFECYCLE", "publication is not permitted")
        elif args.command == "authorize-use":
            authorize_use(
                args.root.resolve(),
                validation_point=args.validation_point,
                at=_parse_time(args.at) if args.at else None,
            )
        elif args.command == "verify-independent-review":
            verify_independent_review(
                args.root.resolve(),
                repository=args.repository,
                commit=args.commit,
                token=os.environ.get("GITHUB_TOKEN", ""),
            )
        elif args.command == "publication-status":
            root = args.root.resolve()
            contract = _load_json(root / CONTRACT_PATH)
            admission = _load_json(root / ADMISSION_PATH)
            print(publication_status(contract, admission))
        elif args.command == "audit-source":
            audit_source(args.root.resolve(), args.checkout.resolve())
        elif args.command == "apply-source-overlay":
            apply_source_overlay(
                args.root.resolve(),
                args.checkout.resolve(),
            )
        elif args.command == "audit-builder-settings":
            audit_builder_settings(args.settings.resolve())
        elif args.command == "audit-transfer-log":
            audit_transfer_log(args.log)
        elif args.command == "verify-server-distribution":
            verify_server_distribution(args.archive.resolve())
        elif args.command == "stage-bun-scan-input":
            stage_bun_scan_input(
                args.checkout.resolve(),
                args.output.resolve(),
            )
        elif args.command == "verify-bun-scan":
            verify_bun_scan(
                args.root.resolve(),
                args.scan_input.resolve(),
                args.raw_report.resolve(),
                args.adjusted_report.resolve(),
            )
        elif args.command == "verify-bun-snapshot":
            verify_bun_snapshot_identity(
                args.descriptor.resolve(),
                args.archive.resolve(),
            )
        elif args.command == "verify-maven-scan":
            verify_maven_scan(
                args.descriptor.resolve(),
                args.sbom.resolve(),
                args.report.resolve(),
            )
        elif args.command == "generate-maven-sbom":
            generate_maven_sbom(
                args.descriptor.resolve(),
                args.repository.resolve(),
                args.rootfs_sbom.resolve(),
                args.output.resolve(),
            )
        else:
            bind_artifact_evidence(
                args.reference,
                args.descriptor.resolve(),
                args.maven_sbom.resolve(),
                args.maven_report.resolve(),
                args.bun_sbom.resolve(),
                args.bun_raw_report.resolve(),
                args.bun_adjusted_report.resolve(),
            )
    except ContractError as error:
        print(f"Trino dependency publisher rejected: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
