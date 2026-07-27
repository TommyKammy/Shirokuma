#!/usr/bin/env python3
"""Fail-closed verifier for the temporary Trino 483 dependency publisher."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


CONTRACT_PATH = Path("bootstrap/trino/v483/trusted-build-contract.json")
ADMISSION_PATH = Path("bootstrap/trino/v483/admission.json")
SETTINGS_PATH = Path("bootstrap/trino/v483/settings.xml")
JVM_CONFIG_PATH = Path("bootstrap/trino/v483/maven-policy/.mvn/jvm.config")
ACTIVE_WORKFLOW_PATH = Path(".github/workflows/trino-maven-dependencies.yml")
WORKFLOW_PATH = Path(
    "bootstrap/trino/v483/dependency-evidence/historical-publisher-workflow.yml"
)
PACKAGER_PATH = Path("scripts/package_trino_maven_dependencies.py")
BUN_PACKAGER_PATH = Path("scripts/package_trino_bun_dependencies.py")
BUN_PREPARER_PATH = Path("scripts/prepare_trino_bun_input.py")
VERIFIER_PATH = Path("scripts/verify_trino_dependency_publisher.py")
TEST_PATH = Path("tests/test_trino_dependency_publisher.py")
BUN_TEST_PATH = Path("tests/test_trino_bun_dependencies.py")
SOURCE_OVERLAY_PATH = Path(
    "bootstrap/trino/v483/patches/0001-shirokuma-web-ui-security.patch"
)
VEX_PATH = Path(
    "bootstrap/trino/v483/vex/"
    "react-router-7.18.1-ghsa-qwww-vcr4-c8h2.openvex.json"
)
OVERLAY_ADR_PATH = Path(
    "docs/design/07_ADR/"
    "ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX.md"
)
EXPECTED_REPOSITORY = "TommyKammy/Shirokuma"
EXPECTED_SOURCE_REPOSITORY = "https://github.com/trinodb/trino"
EXPECTED_TAG = "483"
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
EXPECTED_BUN_PACKAGE_CACHE = {
    "bun_version": "v1.3.14",
    "platform": "linux/arm64",
    "cache_directory": "/bun-cache",
    "registry": "https://registry.npmjs.org/",
    "frozen_lockfiles": [
        {
            "path": "core/trino-web-ui/src/main/resources/webapp/bun.lock",
            "sha256": "b9010ec72590c76c7dc865a10b1fefe554a64eabb1492c422c954e45324cc9d3",
        },
        {
            "path": (
                "core/trino-web-ui/src/main/resources/"
                "webapp-legacy/src/bun.lock"
            ),
            "sha256": "14fa0d75107753676c59093978fe68fe67486868564f41dadc7d76d659d2df25",
        },
    ],
    "independent_reconstructions": 2,
    "reviewed_snapshot": {
        "manifest_sha256": (
            "6e7be3a404014f6f7ac7e4bc326c8d46f7d5822fcea1ac000219c17f1d23f421"
        ),
        "archive_sha256": (
            "252eade2183bdf5a371f073752420c3a45f5ef8b1dacb08a4addea350389e3c2"
        ),
        "archive_size": 128_423_777,
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
        "sha256": "d74d13976a8368c818755d67bbe2f464393c185d87317164bd214108e5d4712d",
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
            "34b237a9af887a5cbe9c83f541a5084299f9eb3b1e974a661dea7cd17a1c8d38"
        ),
        "core/trino-web-ui/src/main/resources/webapp/bun.lock": (
            "b9010ec72590c76c7dc865a10b1fefe554a64eabb1492c422c954e45324cc9d3"
        ),
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/package.json": (
            "34f68bd556c33d54b2e3475ffd8dc45e1a7167b3c9c60ba257ed43e0f0e6a8df"
        ),
        "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock": (
            "14fa0d75107753676c59093978fe68fe67486868564f41dadc7d76d659d2df25"
        ),
    },
    "dependency_overrides": {
        "brace-expansion": "5.0.8",
        "d3-color": "3.1.0",
        "fast-uri": "3.1.4",
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
            "fixed_version": "8.3.0",
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
        "io/trino/benchto/benchto-base/0.34/benchto-base-0.34.pom",
        "io/trino/benchto/benchto-driver/0.34/benchto-driver-0.34.jar",
        "io/trino/benchto/benchto-driver/0.34/benchto-driver-0.34.pom",
        "io/trino/coral/coral/2.2.49-1/coral-2.2.49-1.jar",
        "io/trino/coral/coral/2.2.49-1/coral-2.2.49-1.pom",
        "io/trino/hadoop/hadoop-apache/3.3.5-3/hadoop-apache-3.3.5-3.jar",
        "io/trino/hadoop/hadoop-apache/3.3.5-3/hadoop-apache-3.3.5-3.pom",
        "io/trino/hive/hive-apache-jdbc/0.13.1-10/hive-apache-jdbc-0.13.1-10.jar",
        "io/trino/hive/hive-apache-jdbc/0.13.1-10/hive-apache-jdbc-0.13.1-10.pom",
        "io/trino/hive/hive-apache/3.1.2-23/hive-apache-3.1.2-23.jar",
        "io/trino/hive/hive-apache/3.1.2-23/hive-apache-3.1.2-23.pom",
        "io/trino/hive/hive-thrift/3/hive-thrift-3.jar",
        "io/trino/hive/hive-thrift/3/hive-thrift-3.pom",
        "io/trino/tempto/tempto-core/204/tempto-core-204.jar",
        "io/trino/tempto/tempto-core/204/tempto-core-204.pom",
        "io/trino/tempto/tempto-kafka/204/tempto-kafka-204.jar",
        "io/trino/tempto/tempto-kafka/204/tempto-kafka-204.pom",
        "io/trino/tempto/tempto-ldap/204/tempto-ldap-204.jar",
        "io/trino/tempto/tempto-ldap/204/tempto-ldap-204.pom",
        "io/trino/tempto/tempto-root/204/tempto-root-204.pom",
        "io/trino/tempto/tempto-runner/204/tempto-runner-204.jar",
        "io/trino/tempto/tempto-runner/204/tempto-runner-204.pom",
        "io/trino/tpcds/tpcds/1.7/tpcds-1.7.jar",
        "io/trino/tpcds/tpcds/1.7/tpcds-1.7.pom",
        "io/trino/tpch/tpch/1.4/tpch-1.4.jar",
        "io/trino/tpch/tpch/1.4/tpch-1.4.pom",
        "io/trino/trino-maven-plugin/20/trino-maven-plugin-20.jar",
        "io/trino/trino-maven-plugin/20/trino-maven-plugin-20.pom",
        "io/trino/trino-re2j/1.7/trino-re2j-1.7.jar",
        "io/trino/trino-re2j/1.7/trino-re2j-1.7.pom",
        "io/trino/trino-root/482/trino-root-482.pom",
        "io/trino/trino-spi/482/trino-spi-482.jar",
        "io/trino/trino-spi/482/trino-spi-482.pom",
        "io/trino/trino-spi/maven-metadata-shirokuma-central-fallback.xml",
        "io/trino/trino-spi/maven-metadata-shirokuma-central.xml",
        "io/trino/trino-wasm-python/3.13-7/trino-wasm-python-3.13-7.jar",
        "io/trino/trino-wasm-python/3.13-7/trino-wasm-python-3.13-7.pom",
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
          github.event_name == 'pull_request'
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
        '              echo "Pull requests perform static and Web UI overlay '
        'build validation only"'
    ),
    "        if: github.event_name == 'pull_request'",
    '          CI: "true"',
    "          BUN_CONFIG_REGISTRY: ${{ env.BUN_REGISTRY }}",
    "          BUN_INSTALL_CACHE_DIR: ${{ runner.temp }}/trino-pr-bun-cache",
    '          bun install --frozen-lockfile --cwd "${modern}"',
    '          bun run --cwd "${modern}" typecheck',
    '          bun run --cwd "${modern}" build',
    '          bun run --cwd "${legacy}" package:clean',
)
EXPECTED_OPENVEX_TRIVY_CACHE_BLOCK = """\
      - name: Apply OpenVEX and block remaining Bun High or Critical findings
        if: steps.lifecycle.outputs.active == 'true'
        env:
          TRIVY_INCLUDE_DEV_DEPS: "true"
          TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy
"""
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
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02": 2,
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
        "Validate the bounded Web UI overlay before merge",
        "Resolve and package the first closed Maven repository",
        "Independently reconstruct the closed Maven repository",
        "Prove two fresh network-none offline source builds",
        "Generate a CycloneDX dependency SBOM",
        "Scan the dependency closure and block High or Critical findings",
        "Stage the exact Bun lockfiles for dependency analysis",
        "Generate a CycloneDX Bun dependency SBOM",
        "Retain the raw Bun High or Critical findings",
        "Apply OpenVEX and block remaining Bun High or Critical findings",
        "Verify the raw and OpenVEX-adjusted Bun dependency evidence",
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
EXPECTED_RESOLUTION_COMMAND = (
    "mvn --batch-mode --show-version --errors --strict-checksums "
    "--ignore-transitive-repositories "
    "--settings /policy/settings.xml -Dmaven.repo.local=/m2 "
    "--file /workspace/pom.xml -pl '!:trino-docs' "
    "clean install -DskipTests"
)


class ContractError(ValueError):
    """Raised when the publisher no longer matches its reviewed contract."""


def _fail(code: str, detail: str) -> None:
    raise ContractError(f"{code}: {detail}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail("JSON", f"{path}: {error}")
    if not isinstance(value, dict):
        _fail("JSON", f"{path} root must be an object")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        _fail("POLICY_FILE", f"{path}: {error}")


def _read_reviewed_regular_file(path: Path, *, code: str) -> bytes:
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
            payload = source.read()
        if len(payload) != observed.st_size:
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
    if document != expected_document:
        _fail("SOURCE_VEX", "OpenVEX document differs")


def _validate_source_overlay_contract(
    root: Path,
    contract: Mapping[str, Any],
    *,
    at: dt.datetime | None,
) -> None:
    overlay = contract.get("source", {}).get("source_overlay")
    if overlay != EXPECTED_SOURCE_OVERLAY:
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
    _validate_source_overlay_contract(root, contract, at=now)
    overlay = EXPECTED_SOURCE_OVERLAY
    permitted = set(overlay["permitted_paths"])
    if permitted != set(overlay["preimages"]) or permitted != set(
        overlay["postimages"]
    ):
        _fail("SOURCE_OVERLAY", "preimage/postimage path sets differ")
    for relative, expected in overlay["preimages"].items():
        payload = _read_reviewed_regular_file(
            checkout / relative,
            code="SOURCE_OVERLAY_PREIMAGE",
        )
        if hashlib.sha256(payload).hexdigest() != expected:
            _fail("SOURCE_OVERLAY_PREIMAGE", relative)
    _validate_react_router_import_inventory(checkout)
    patch = root / overlay["patch"]["path"]
    command = [
        "git",
        "apply",
        *overlay["apply_arguments"],
        str(patch),
    ]
    try:
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
    for relative, expected in overlay["postimages"].items():
        payload = _read_reviewed_regular_file(
            checkout / relative,
            code="SOURCE_OVERLAY_POSTIMAGE",
        )
        if hashlib.sha256(payload).hexdigest() != expected:
            _fail("SOURCE_OVERLAY_POSTIMAGE", relative)
    _validate_react_router_import_inventory(checkout)


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
        report.get("SchemaVersion") != 2
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
    if not isinstance(authorization, dict):
        _fail("AUTHORIZATION", "authorization record is missing")
    approved = _parse_time(authorization.get("approved_at", ""))
    expires = _parse_time(authorization.get("expires_at", ""))
    if (
        authorization.get("type")
        != "time_boxed_source_identity_risk_acceptance"
        or authorization.get("issue")
        != "https://github.com/TommyKammy/Shirokuma/issues/63"
        or authorization.get("maximum_duration_days") != 30
        or authorization.get("automatic_renewal") is not False
        or authorization.get("risk_owner") != "TommyKammy"
        or authorization.get("implementation_author") != "Codex"
        or expires - approved > dt.timedelta(days=30)
        or approved >= expires
    ):
        _fail("AUTHORIZATION", "time-boxed Issue #63 authorization differs")
    review = authorization.get("review", {})
    if (
        review.get("required_before_merge") is not True
        or review.get("reviewer_must_differ_from_implementation_author") is not True
    ):
        _fail("AUTHORIZATION", "owner/reviewer separation is missing")
    if at is not None and not approved <= at < expires:
        _fail(
            "AUTHORIZATION_EXPIRED",
            f"{at.isoformat()} is outside [{approved.isoformat()}, {expires.isoformat()})",
        )


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
    if (
        block.count(maven_marker) != 1
        or observed_network_none != (1 if network_none else 0)
        or block.count(f"  {EXPECTED_SETTINGS_MOUNT} \\\n") != 1
    ):
        _fail(code, "Maven builder invocation differs")
    arguments = block.split(maven_marker, 1)[1]
    normalized = " ".join(arguments.replace("\\\n", " ").split())
    if not normalized:
        _fail(code, "Maven arguments are missing")
    return f"mvn {normalized}"


def _offline_maven_command(workflow: str) -> str:
    return _maven_command_before_marker(
        workflow,
        (
            "            python3 scripts/"
            "package_trino_bun_dependencies.py verify-cache \\"
        ),
        code="WORKFLOW_OFFLINE_COMMAND",
        network_none=True,
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
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
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
        workflow.count(EXPECTED_PR_SOURCE_CONDITION) != 2
        or workflow.count(EXPECTED_PR_BUN_INPUT_BLOCK) != 1
        or any(
            workflow.count(marker) != 1
            for marker in EXPECTED_PR_OVERLAY_BUILD_MARKERS
        )
    ):
        _fail(
            "WORKFLOW_PR_OVERLAY_VALIDATION",
            (
                "pull requests must fetch, apply, and build the exact Web UI "
                "overlay without enabling publication"
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
        ACTIVE_WORKFLOW_PATH,
        CONTRACT_PATH,
        ADMISSION_PATH,
        JVM_CONFIG_PATH,
        SETTINGS_PATH,
        PACKAGER_PATH,
        BUN_PACKAGER_PATH,
        BUN_PREPARER_PATH,
        VERIFIER_PATH,
        TEST_PATH,
        BUN_TEST_PATH,
        SOURCE_OVERLAY_PATH,
        VEX_PATH,
        OVERLAY_ADR_PATH,
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
        EXPECTED_COMMIT,
        EXPECTED_TREE,
        EXPECTED_BUILDER,
        "--entrypoint /usr/share/maven/bin/mvn",
        "--env MAVEN_CONFIG=/tmp/maven-home/.m2",
        "--workdir /policy",
        "--file /workspace/pom.xml",
        "python3 scripts/verify_trino_dependency_publisher.py authorize",
        "python3 scripts/verify_trino_dependency_publisher.py audit-builder-settings",
        "python3 scripts/verify_trino_dependency_publisher.py audit-transfer-log",
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
        "--type slsaprovenance1",
        '"https://slsa.dev/provenance/v1"',
        '"https://in-toto.io/Statement/v1"',
        "verified SLSA v1 payload does not uniquely bind",
        "predicate.buildDefinition.resolvedDependencies",
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
        workflow.count(EXPECTED_SETTINGS_MOUNT) != 3
        or workflow.count(f"{EXPECTED_SETTINGS_ARGUMENT} \\\n") != 3
    ):
        _fail(
            "WORKFLOW_SETTINGS",
            (
                "both online resolvers and the two-run network-none rebuild "
                "must use the exact read-only repository settings"
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
        offline_rebuild.get("repository_settings")
        != EXPECTED_OFFLINE_REPOSITORY_SETTINGS
        or offline_rebuild.get("bun_cache") != EXPECTED_OFFLINE_BUN_CACHE
        or offline_rebuild.get("compiler_debug_information")
        != EXPECTED_OFFLINE_COMPILER_DEBUG
    ):
        _fail(
            "WORKFLOW_SETTINGS",
            "contract offline build settings differ",
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
        workflow.count("--network none") != 1
        or "for suffix in a b; do" not in workflow
        or '"fresh_source_checkouts": 2' not in workflow
        or '"fresh_snapshot_extractions": 2' not in workflow
        or workflow.count('"compiler_debug_level": "source,lines"') != 1
        or workflow.count('"local_variable_debug_table_permitted": False') != 1
        or workflow.count("-Dmaven.compiler.debuglevel=source,lines") != 2
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
        or workflow.count('--env CI=true \\') != 3
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
        workflow.count(EXPECTED_OPENVEX_TRIVY_CACHE_BLOCK) != 1
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
        or workflow.count("list-all-pkgs: true") != 2
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
        or publication.get("workflow_present") is not False
        or publication.get("workflow") != ACTIVE_WORKFLOW_PATH.as_posix()
        or publication.get("historical_workflow") != WORKFLOW_PATH.as_posix()
        or publication.get("retired") is not True
        or publication.get("allowed_ref") != "refs/heads/main"
        or publication.get("artifact_role") != "review_pending_dependency_evidence"
        or publication.get("retire_in_evidence_review_pr") is not True
    ):
        _fail("PUBLICATION", "retired publication lifecycle differs")


def _validate_policy_hashes(root: Path, contract: Mapping[str, Any]) -> None:
    expected_paths = {
        SETTINGS_PATH,
        JVM_CONFIG_PATH,
        PACKAGER_PATH,
        BUN_PACKAGER_PATH,
        BUN_PREPARER_PATH,
        VERIFIER_PATH,
        TEST_PATH,
        BUN_TEST_PATH,
        SOURCE_OVERLAY_PATH,
        VEX_PATH,
        OVERLAY_ADR_PATH,
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
    lifecycle = contract.get("lifecycle", {})
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
    if contract.get("toolchain", {}).get("builder", {}).get("index") != EXPECTED_BUILDER:
        _fail("BUILDER", "builder index differs")
    dependency_resolution = contract.get("dependency_resolution", {})
    reactor_outputs = dependency_resolution.get("reactor_outputs", {})
    if (
        dependency_resolution.get("repositories")
        != list(EXPECTED_REPOSITORIES.values())
        or dependency_resolution.get("transitive_dependency_repositories_ignored")
        is not True
        or dependency_resolution.get("repository_mirrors")
        != [
            {
                "id": values[0][1],
                "mirror_of": values[1][1],
                "url": values[3][1],
            }
            for values in EXPECTED_REPOSITORY_MIRRORS
        ]
        or dependency_resolution.get("settings_policy")
        != EXPECTED_SETTINGS_POLICY
        or dependency_resolution.get("external_inputs") != [EXPECTED_BUN_INPUT]
        or dependency_resolution.get("bun_package_cache")
        != EXPECTED_BUN_PACKAGE_CACHE
        or reactor_outputs.get("repository_path_prefix") != "io/trino/"
        or reactor_outputs.get("dependency_input_permitted") is not False
        or reactor_outputs.get("rebuild_from_reviewed_source_required") is not True
        or reactor_outputs.get("exact_external_build_extension")
        != EXPECTED_TRINO_BUILD_EXTENSION
        or reactor_outputs.get("exact_external_maven_inputs")
        != EXPECTED_TRINO_EXTERNAL_MAVEN_INPUTS
    ):
        _fail("REPOSITORIES", "contract repository allowlist differs")
    snapshot = contract.get("snapshot", {})
    if (
        snapshot.get("artifact_type") != EXPECTED_ARTIFACT_TYPE
        or snapshot.get("descriptor_media_type")
        != EXPECTED_DESCRIPTOR_MEDIA_TYPE
        or snapshot.get("manifest", {}).get("schema_version") != 2
        or snapshot.get("bun_cache")
        != {
            "descriptor_media_type": EXPECTED_BUN_DESCRIPTOR_MEDIA_TYPE,
            "archive_media_type": EXPECTED_BUN_ARCHIVE_MEDIA_TYPE,
            "manifest_schema_version": 1,
        }
    ):
        _fail("SNAPSHOT_FORMAT", "dependency snapshot v2 contract differs")
    if snapshot.get("visibility_bootstrap") != {
        "required_visibility": "public",
        "sign_and_attest_before_anonymous_pull": True,
        "owner_action_on_first_private_run": "set-package-public-and-rerun",
        "failed_attempt_admitted": False,
        "user_credential_fallback": False,
    }:
        _fail("VISIBILITY", "first-publication visibility contract differs")
    repository_state = admission.get("repository_state", {})
    if (
        admission.get("source_overlay_authorization")
        != EXPECTED_ADMISSION_OVERLAY_AUTHORIZATION
        or repository_state.get("publication_workflow_permitted") is not False
        or repository_state.get("dependency_artifact_present") is not True
        or repository_state.get("resident_ledger_permitted") is not False
        or repository_state.get("runtime_manifests_permitted") is not False
    ):
        _fail("ADMISSION", "admission state crosses the publisher boundary")
    _validate_settings(root)
    _validate_policy_hashes(root, contract)
    if (root / ACTIVE_WORKFLOW_PATH).exists():
        _fail("WORKFLOW", "retired write-capable publisher was reintroduced")
    try:
        workflow = (root / WORKFLOW_PATH).read_text(encoding="utf-8")
    except OSError as error:
        _fail("WORKFLOW", str(error))
    _validate_workflow(contract, workflow)


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
        or tag_object != "32d4f28e8311ea6f67edca209df59a0493d869fa"
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
    if observed == 0:
        _fail("TRANSFER_LOG", "no Maven repository transfers were observed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--root", type=Path, default=Path("."))
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--root", type=Path, default=Path("."))
    authorize.add_argument("--at")
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
            if contract.get("lifecycle", {}).get("state") != (
                "dependency_snapshot_publication_pending"
            ):
                _fail("LIFECYCLE", "publisher is retired or not approved")
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
        else:
            verify_bun_snapshot_identity(
                args.descriptor.resolve(),
                args.archive.resolve(),
            )
    except ContractError as error:
        print(f"Trino dependency publisher rejected: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
