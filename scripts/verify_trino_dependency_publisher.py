#!/usr/bin/env python3
"""Fail-closed verifier for the temporary Trino 483 dependency publisher."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import textwrap
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit


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
TEST_PATH = Path("tests/test_trino_dependency_publisher.py")
BUN_TEST_PATH = Path("tests/test_trino_bun_dependencies.py")
PARQUET_REMEDIATION_TEST_PATH = Path(
    "tests/test_parquet_jackson_remediation.py"
)
MAX_OMITTED_JAR_MEMBER_BYTES = 64 * 1024 * 1024
MAX_OMITTED_JAR_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_OMITTED_JAR_COMPRESSION_RATIO = 200
MAX_OMITTED_JAR_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_OMITTED_JAR_MEMBERS = 100_000
MAX_OMITTED_JAR_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_OMITTED_CLASS_LOCALS = 1024
MAX_OMITTED_CLASS_STATE_CELLS = 1_000_000
PINNED_JAVA_CLASS_MAJOR_VERSION = 69
SOURCE_OVERLAY_PATH = Path(
    "bootstrap/trino/v483/patches/0001-shirokuma-web-ui-security.patch"
)
DISTRIBUTION_REMEDIATION_PATH = Path(
    "bootstrap/trino/v483/patches/"
    "0002-shirokuma-iceberg-only-maven-closure.patch"
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
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02": 3,
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


def _validate_source_remediation_contract(
    contract: Mapping[str, Any],
    *,
    at: dt.datetime | None,
) -> None:
    remediation = contract.get("source_remediation")
    if remediation != EXPECTED_SOURCE_REMEDIATION:
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
    if remediation != EXPECTED_DISTRIBUTION_REMEDIATION:
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
    _validate_distribution_remediation_contract(root, contract, at=now)
    overlay = EXPECTED_SOURCE_OVERLAY
    distribution = EXPECTED_DISTRIBUTION_REMEDIATION
    permitted: set[str] = set()
    for boundary in (overlay, distribution):
        boundary_permitted = set(boundary["permitted_paths"])
        if boundary_permitted != set(
            boundary["preimages"]
        ) or boundary_permitted != set(boundary["postimages"]):
            _fail("SOURCE_OVERLAY", "preimage/postimage path sets differ")
        if permitted & boundary_permitted:
            _fail("SOURCE_OVERLAY", "overlay path boundaries overlap")
        permitted |= boundary_permitted
        for relative, expected in boundary["preimages"].items():
            payload = _read_reviewed_regular_file(
                checkout / relative,
                code="SOURCE_OVERLAY_PREIMAGE",
            )
            if hashlib.sha256(payload).hexdigest() != expected:
                _fail("SOURCE_OVERLAY_PREIMAGE", relative)
    _validate_react_router_import_inventory(checkout)
    try:
        for boundary in (overlay, distribution):
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
    for boundary in (overlay, distribution):
        for relative, expected in boundary["postimages"].items():
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


def _maven_jar_records(
    descriptor_path: Path,
) -> dict[str, Mapping[str, Any]]:
    descriptor = _load_json(descriptor_path)
    files = descriptor.get("files")
    if (
        descriptor.get("schema_version") != 2
        or not isinstance(files, list)
        or descriptor.get("file_count") != len(files)
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
            if (
                re.fullmatch(r"0[0-7]{3}", str(record.get("mode"))) is None
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(record.get("sha256")),
                )
                is None
                or not isinstance(record.get("size"), int)
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


def _valid_modified_utf8(payload: bytes) -> bool:
    offset = 0
    while offset < len(payload):
        first = payload[offset]
        if 0x01 <= first <= 0x7F:
            offset += 1
            continue
        if first == 0xC0:
            if offset + 1 >= len(payload) or payload[offset + 1] != 0x80:
                return False
            offset += 2
            continue
        if 0xC2 <= first <= 0xDF:
            if (
                offset + 1 >= len(payload)
                or not 0x80 <= payload[offset + 1] <= 0xBF
            ):
                return False
            offset += 2
            continue
        if 0xE0 <= first <= 0xEF:
            if offset + 2 >= len(payload):
                return False
            second = payload[offset + 1]
            third = payload[offset + 2]
            if (
                (first == 0xE0 and not 0xA0 <= second <= 0xBF)
                or (first != 0xE0 and not 0x80 <= second <= 0xBF)
                or not 0x80 <= third <= 0xBF
            ):
                return False
            offset += 3
            continue
        return False
    return True


def _encode_modified_utf8(value: str) -> bytes:
    encoded = bytearray()
    utf16 = value.encode("utf-16-be", "surrogatepass")
    for offset in range(0, len(utf16), 2):
        code_unit = int.from_bytes(utf16[offset : offset + 2], "big")
        if 0x01 <= code_unit <= 0x7F:
            encoded.append(code_unit)
        elif code_unit <= 0x07FF:
            encoded.extend(
                (
                    0xC0 | code_unit >> 6,
                    0x80 | code_unit & 0x3F,
                )
            )
        else:
            encoded.extend(
                (
                    0xE0 | code_unit >> 12,
                    0x80 | code_unit >> 6 & 0x3F,
                    0x80 | code_unit & 0x3F,
                )
            )
    return bytes(encoded)


def _bytecode_instruction_offsets(
    payload: bytes,
    *,
    constant_pool_tags: list[int] | None = None,
    constant_pool_values: list[object] | None = None,
    invokeinterface_counts: Mapping[int, int] | None = None,
    major_version: int = 52,
) -> set[int] | None:
    zero_operand = (
        set(range(0x00, 0x10))
        | set(range(0x1A, 0x36))
        | set(range(0x3B, 0x84))
        | set(range(0x85, 0x99))
        | set(range(0xAC, 0xB2))
        | {0xBE, 0xBF, 0xC2, 0xC3}
    )
    one_operand = (
        {0x10, 0x12, 0xA9, 0xBC}
        | set(range(0x15, 0x1A))
        | set(range(0x36, 0x3B))
    )
    two_operands = (
        {0x11, 0x13, 0x14, 0x84, 0xBB, 0xBD, 0xC0, 0xC1, 0xC6, 0xC7}
        | set(range(0x99, 0xA9))
        | set(range(0xB2, 0xB9))
    )
    branch_16 = set(range(0x99, 0xA9)) | {0xC6, 0xC7}
    starts: set[int] = set()
    branch_targets: list[int] = []
    offset = 0

    def read(size: int) -> bytes:
        nonlocal offset
        end = offset + size
        if end > len(payload):
            raise ValueError("truncated bytecode instruction")
        value = payload[offset:end]
        offset = end
        return value

    def read_signed(size: int) -> int:
        return int.from_bytes(read(size), "big", signed=True)

    def has_constant_tag(pool_index: int, *expected: int) -> bool:
        return (
            constant_pool_tags is None
            or (
                0 < pool_index < len(constant_pool_tags)
                and constant_pool_tags[pool_index] in expected
            )
        )

    def dynamic_constant_slots(pool_index: int) -> int | None:
        if (
            constant_pool_tags is None
            or constant_pool_values is None
            or not 0 < pool_index < len(constant_pool_tags)
            or constant_pool_tags[pool_index] != 17
        ):
            return None
        dynamic = constant_pool_values[pool_index]
        if not isinstance(dynamic, tuple) or len(dynamic) != 2:
            return None
        name_and_type_index = dynamic[1]
        if (
            not isinstance(name_and_type_index, int)
            or not 0 < name_and_type_index < len(constant_pool_values)
        ):
            return None
        name_and_type = constant_pool_values[name_and_type_index]
        if not isinstance(name_and_type, tuple) or len(name_and_type) != 2:
            return None
        descriptor_index = name_and_type[1]
        if (
            not isinstance(descriptor_index, int)
            or not 0 < descriptor_index < len(constant_pool_values)
        ):
            return None
        descriptor = constant_pool_values[descriptor_index]
        slots = (
            _field_descriptor_stack_slots(descriptor)
            if isinstance(descriptor, bytes)
            else None
        )
        return len(slots) if slots is not None else None

    def class_array_dimensions(pool_index: int) -> int | None:
        if constant_pool_values is None:
            return None
        if not 0 < pool_index < len(constant_pool_values):
            return None
        class_value = constant_pool_values[pool_index]
        if not isinstance(class_value, tuple) or len(class_value) != 1:
            return None
        name_index = class_value[0]
        if (
            not isinstance(name_index, int)
            or not 0 < name_index < len(constant_pool_values)
        ):
            return None
        name = constant_pool_values[name_index]
        if not isinstance(name, bytes):
            return None
        return len(name) - len(name.lstrip(b"["))

    try:
        while offset < len(payload):
            instruction_offset = offset
            starts.add(instruction_offset)
            opcode = read(1)[0]
            if opcode in zero_operand:
                continue
            if opcode in one_operand:
                operand = read(1)[0]
                if opcode == 0xA9 and major_version >= 51:
                    return None
                if opcode == 0xBC and operand not in range(4, 12):
                    return None
                if opcode == 0x12 and not has_constant_tag(
                    operand,
                    3,
                    4,
                    7,
                    8,
                    15,
                    16,
                    17,
                ):
                    return None
                if (
                    opcode == 0x12
                    and constant_pool_tags is not None
                    and constant_pool_values is not None
                    and constant_pool_tags[operand] == 17
                    and dynamic_constant_slots(operand) != 1
                ):
                    return None
                continue
            if opcode in two_operands:
                if opcode in branch_16:
                    if opcode == 0xA8 and major_version >= 51:
                        return None
                    branch_targets.append(
                        instruction_offset + read_signed(2)
                    )
                else:
                    operand = int.from_bytes(read(2), "big")
                    expected_tags: tuple[int, ...] | None = None
                    if opcode == 0x13:
                        expected_tags = (3, 4, 7, 8, 15, 16, 17)
                    elif opcode == 0x14:
                        expected_tags = (5, 6, 17)
                    elif opcode in range(0xB2, 0xB6):
                        expected_tags = (9,)
                    elif opcode == 0xB6:
                        expected_tags = (10,)
                    elif opcode in {0xB7, 0xB8}:
                        expected_tags = (
                            (10, 11) if major_version >= 52 else (10,)
                        )
                    elif opcode in {0xBB, 0xBD, 0xC0, 0xC1}:
                        expected_tags = (7,)
                    if expected_tags is not None and not has_constant_tag(
                        operand,
                        *expected_tags,
                    ):
                        return None
                    if (
                        opcode in {0x13, 0x14}
                        and constant_pool_tags is not None
                        and constant_pool_values is not None
                        and constant_pool_tags[operand] == 17
                        and dynamic_constant_slots(operand)
                        != (2 if opcode == 0x14 else 1)
                    ):
                        return None
                continue
            if opcode == 0xB9:
                operands = read(4)
                pool_index = int.from_bytes(operands[:2], "big")
                if (
                    not has_constant_tag(
                        pool_index,
                        11,
                    )
                    or operands[2] == 0
                    or operands[3] != 0
                    or (
                        invokeinterface_counts is not None
                        and invokeinterface_counts.get(pool_index)
                        != operands[2]
                    )
                ):
                    return None
                continue
            if opcode == 0xBA:
                operands = read(4)
                if (
                    not has_constant_tag(
                        int.from_bytes(operands[:2], "big"),
                        18,
                    )
                    or operands[2:] != b"\x00\x00"
                ):
                    return None
                continue
            if opcode == 0xC5:
                operands = read(3)
                pool_index = int.from_bytes(operands[:2], "big")
                dimensions = class_array_dimensions(pool_index)
                if (
                    not has_constant_tag(
                        pool_index,
                        7,
                    )
                    or operands[2] == 0
                    or (
                        constant_pool_values is not None
                        and (
                            dimensions is None
                            or dimensions < operands[2]
                        )
                    )
                ):
                    return None
                continue
            if opcode in {0xC8, 0xC9}:
                if opcode == 0xC9 and major_version >= 51:
                    return None
                branch_targets.append(
                    instruction_offset + read_signed(4)
                )
                continue
            if opcode == 0xC4:
                widened_opcode = read(1)[0]
                if widened_opcode == 0xA9 and major_version >= 51:
                    return None
                if widened_opcode == 0x84:
                    read(4)
                elif widened_opcode in (
                    set(range(0x15, 0x1A))
                    | set(range(0x36, 0x3B))
                    | {0xA9}
                ):
                    read(2)
                else:
                    return None
                continue
            if opcode in {0xAA, 0xAB}:
                padding_size = (4 - (offset % 4)) % 4
                if read(padding_size) != b"\x00" * padding_size:
                    return None
                branch_targets.append(
                    instruction_offset + read_signed(4)
                )
                if opcode == 0xAA:
                    low = read_signed(4)
                    high = read_signed(4)
                    if high < low:
                        return None
                    for _ in range(high - low + 1):
                        branch_targets.append(
                            instruction_offset + read_signed(4)
                        )
                else:
                    pair_count = read_signed(4)
                    if pair_count < 0:
                        return None
                    previous_key: int | None = None
                    for _ in range(pair_count):
                        key = read_signed(4)
                        if previous_key is not None and key <= previous_key:
                            return None
                        previous_key = key
                        branch_targets.append(
                            instruction_offset + read_signed(4)
                        )
                continue
            return None
    except ValueError:
        return None
    if any(target not in starts for target in branch_targets):
        return None
    return starts


def _valid_internal_name(payload: bytes) -> bool:
    return bool(payload) and all(
        part
        and b"." not in part
        and b";" not in part
        and b"[" not in part
        for part in payload.split(b"/")
    )


def _valid_unqualified_name(payload: bytes, *, method: bool) -> bool:
    if (
        not payload
        or any(character in payload for character in b".;[/")
    ):
        return False
    if not method:
        return True
    return (
        payload in {b"<init>", b"<clinit>"}
        or b"<" not in payload
        and b">" not in payload
    )


def _field_descriptor_end(
    payload: bytes,
    offset: int = 0,
) -> tuple[int, int] | None:
    dimensions = 0
    while offset < len(payload) and payload[offset] == ord("["):
        dimensions += 1
        if dimensions > 255:
            return None
        offset += 1
    if offset >= len(payload):
        return None
    descriptor_type = payload[offset]
    if descriptor_type in b"BCFISZ":
        return offset + 1, 1
    if descriptor_type in b"DJ":
        return offset + 1, 1 if dimensions else 2
    if descriptor_type != ord("L"):
        return None
    end = payload.find(b";", offset + 1)
    if end < 0:
        return None
    internal_name = payload[offset + 1 : end]
    if not _valid_internal_name(internal_name):
        return None
    return end + 1, 1


def _valid_field_descriptor(payload: bytes) -> bool:
    parsed = _field_descriptor_end(payload)
    return parsed is not None and parsed[0] == len(payload)


def _method_descriptor_parameter_slots(
    payload: bytes,
    *,
    max_parameter_slots: int = 255,
) -> int | None:
    if not payload.startswith(b"("):
        return None
    offset = 1
    parameter_slots = 0
    while offset < len(payload) and payload[offset] != ord(")"):
        parsed = _field_descriptor_end(payload, offset)
        if parsed is None:
            return None
        offset, slots = parsed
        parameter_slots += slots
        if parameter_slots > max_parameter_slots:
            return None
    if offset >= len(payload) or payload[offset] != ord(")"):
        return None
    offset += 1
    if offset < len(payload) and payload[offset] == ord("V"):
        return parameter_slots if offset + 1 == len(payload) else None
    return_type = _field_descriptor_end(payload, offset)
    return (
        parameter_slots
        if return_type is not None and return_type[0] == len(payload)
        else None
    )


def _method_descriptor_parameter_count(payload: bytes) -> int | None:
    if _method_descriptor_parameter_slots(payload) is None:
        return None
    offset = 1
    parameter_count = 0
    while payload[offset] != ord(")"):
        parsed = _field_descriptor_end(payload, offset)
        if parsed is None:
            return None
        offset = parsed[0]
        parameter_count += 1
    return parameter_count


def _valid_method_descriptor(
    payload: bytes,
    *,
    max_parameter_slots: int = 255,
) -> bool:
    return (
        _method_descriptor_parameter_slots(
            payload,
            max_parameter_slots=max_parameter_slots,
        )
        is not None
    )


def _method_descriptor_return_slots(payload: bytes) -> int | None:
    closing = payload.find(b")")
    if closing < 0 or closing + 1 >= len(payload):
        return None
    return_descriptor = payload[closing + 1 :]
    if return_descriptor == b"V":
        return 0
    parsed = _field_descriptor_end(return_descriptor)
    return (
        parsed[1]
        if parsed is not None and parsed[0] == len(return_descriptor)
        else None
    )


def _method_descriptor_return_opcode(payload: bytes) -> int | None:
    closing = payload.find(b")")
    if closing < 0 or closing + 1 >= len(payload):
        return None
    return_descriptor = payload[closing + 1 :]
    if return_descriptor == b"V":
        return 0xB1
    if not _valid_field_descriptor(return_descriptor):
        return None
    if return_descriptor.startswith((b"L", b"[")):
        return 0xB0
    return {
        ord("J"): 0xAD,
        ord("F"): 0xAE,
        ord("D"): 0xAF,
    }.get(return_descriptor[0], 0xAC)


def _field_descriptor_stack_slots(payload: bytes) -> tuple[str, ...] | None:
    if not _valid_field_descriptor(payload):
        return None
    if payload.startswith((b"L", b"[")):
        return (f"reference:{payload.decode('latin-1')}",)
    return {
        ord("F"): ("float",),
        ord("J"): ("long", "long"),
        ord("D"): ("double", "double"),
    }.get(payload[0], ("int",))


def _method_descriptor_stack_slots(
    payload: bytes,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    if not _valid_method_descriptor(payload):
        return None
    offset = 1
    parameters: tuple[str, ...] = ()
    while payload[offset] != ord(")"):
        parsed = _field_descriptor_end(payload, offset)
        if parsed is None:
            return None
        end, _ = parsed
        slots = _field_descriptor_stack_slots(payload[offset:end])
        if slots is None:
            return None
        parameters += slots
        offset = end
    return_descriptor = payload[offset + 1 :]
    if return_descriptor == b"V":
        return parameters, ()
    return_slots = _field_descriptor_stack_slots(return_descriptor)
    return (
        (parameters, return_slots)
        if return_slots is not None
        else None
    )


def _bytecode_resource_requirements(
    payload: bytes,
    *,
    instruction_offsets: set[int],
    constant_pool_values: list[object],
) -> tuple[int, int] | None:
    offsets = sorted(instruction_offsets)
    minimum_stack = 0
    minimum_locals = 0

    def require_stack(*depths: int) -> None:
        nonlocal minimum_stack
        minimum_stack = max(minimum_stack, *depths)

    def require_local(index: int, slots: int) -> None:
        nonlocal minimum_locals
        minimum_locals = max(minimum_locals, index + slots)

    def constant(index: int) -> object | None:
        return (
            constant_pool_values[index]
            if 0 < index < len(constant_pool_values)
            else None
        )

    def referenced_descriptor(index: int) -> bytes | None:
        reference = constant(index)
        if not isinstance(reference, tuple) or len(reference) != 2:
            return None
        name_and_type = constant(reference[1])
        if not isinstance(name_and_type, tuple) or len(name_and_type) != 2:
            return None
        descriptor = constant(name_and_type[1])
        return descriptor if isinstance(descriptor, bytes) else None

    try:
        for position, instruction_offset in enumerate(offsets):
            end = (
                offsets[position + 1]
                if position + 1 < len(offsets)
                else len(payload)
            )
            instruction = payload[instruction_offset:end]
            opcode = instruction[0]

            if opcode in {0x09, 0x0A, 0x0E, 0x0F}:
                require_stack(2)
            elif opcode in set(range(0x01, 0x09)) | set(range(0x0B, 0x0E)):
                require_stack(1)
            elif opcode in {0x10, 0x11, 0x12, 0x13}:
                require_stack(1)
            elif opcode == 0x14:
                require_stack(2)
            elif opcode in range(0x15, 0x1A):
                slots = 2 if opcode in {0x16, 0x18} else 1
                require_local(instruction[1], slots)
                require_stack(slots)
            elif opcode in range(0x1A, 0x2E):
                group = (opcode - 0x1A) // 4
                slots = 2 if group in {1, 3} else 1
                require_local((opcode - 0x1A) % 4, slots)
                require_stack(slots)
            elif opcode in range(0x2E, 0x36):
                slots = 2 if opcode in {0x2F, 0x31} else 1
                require_stack(2, slots)
            elif opcode in range(0x36, 0x3B):
                slots = 2 if opcode in {0x37, 0x39} else 1
                require_local(instruction[1], slots)
                require_stack(slots)
            elif opcode in range(0x3B, 0x4F):
                group = (opcode - 0x3B) // 4
                slots = 2 if group in {1, 3} else 1
                require_local((opcode - 0x3B) % 4, slots)
                require_stack(slots)
            elif opcode in range(0x4F, 0x57):
                slots = 2 if opcode in {0x50, 0x52} else 1
                require_stack(2 + slots)
            elif opcode == 0x57:
                require_stack(1)
            elif opcode == 0x58:
                require_stack(2)
            elif opcode == 0x59:
                require_stack(2)
            elif opcode == 0x5A:
                require_stack(3)
            elif opcode == 0x5B:
                require_stack(4)
            elif opcode == 0x5C:
                require_stack(4)
            elif opcode == 0x5D:
                require_stack(5)
            elif opcode == 0x5E:
                require_stack(6)
            elif opcode == 0x5F:
                require_stack(2)
            elif opcode in (
                set(range(0x60, 0x74))
                | set(range(0x78, 0x7E))
                | set(range(0x7E, 0x84))
            ):
                if opcode in {
                    0x61,
                    0x63,
                    0x65,
                    0x67,
                    0x69,
                    0x6B,
                    0x6D,
                    0x6F,
                    0x71,
                    0x73,
                    0x75,
                    0x77,
                    0x7F,
                    0x81,
                    0x83,
                }:
                    require_stack(4)
                elif opcode in {0x79, 0x7B, 0x7D}:
                    require_stack(3)
                else:
                    require_stack(2)
            elif opcode in range(0x74, 0x78):
                require_stack(2 if opcode in {0x75, 0x77} else 1)
            elif opcode == 0x84:
                require_local(instruction[1], 1)
            elif opcode in {0x85, 0x87, 0x8C, 0x8D, 0x8F, 0x90}:
                require_stack(2)
            elif opcode in {0x86, 0x8B, 0x91, 0x92, 0x93}:
                require_stack(1)
            elif opcode in {0x88, 0x89, 0x8A, 0x8E}:
                require_stack(2)
            elif opcode == 0x94 or opcode in {0x97, 0x98}:
                require_stack(4)
            elif opcode in {0x95, 0x96}:
                require_stack(2)
            elif opcode in set(range(0x99, 0x9F)) | {0xAA, 0xAB, 0xAC, 0xAE, 0xB0, 0xBF, 0xC2, 0xC3, 0xC6, 0xC7}:
                require_stack(1)
            elif opcode in set(range(0x9F, 0xA7)):
                require_stack(2)
            elif opcode in {0xA8, 0xC9}:
                require_stack(1)
            elif opcode == 0xA9:
                require_local(instruction[1], 1)
            elif opcode in {0xAD, 0xAF}:
                require_stack(2)
            elif opcode in range(0xB2, 0xB6):
                descriptor = referenced_descriptor(
                    int.from_bytes(instruction[1:3], "big")
                )
                parsed = (
                    _field_descriptor_end(descriptor)
                    if isinstance(descriptor, bytes)
                    else None
                )
                if parsed is None or parsed[0] != len(descriptor):
                    return None
                slots = parsed[1]
                if opcode == 0xB2:
                    require_stack(slots)
                elif opcode == 0xB3:
                    require_stack(slots)
                elif opcode == 0xB4:
                    require_stack(1, slots)
                else:
                    require_stack(1 + slots)
            elif opcode in range(0xB6, 0xBB):
                descriptor = referenced_descriptor(
                    int.from_bytes(instruction[1:3], "big")
                )
                parameter_slots = (
                    _method_descriptor_parameter_slots(descriptor)
                    if isinstance(descriptor, bytes)
                    else None
                )
                return_slots = (
                    _method_descriptor_return_slots(descriptor)
                    if isinstance(descriptor, bytes)
                    else None
                )
                if parameter_slots is None or return_slots is None:
                    return None
                receiver_slots = 0 if opcode in {0xB8, 0xBA} else 1
                require_stack(
                    parameter_slots + receiver_slots,
                    return_slots,
                )
            elif opcode == 0xBB:
                require_stack(1)
            elif opcode in {0xBC, 0xBD, 0xBE, 0xC0, 0xC1}:
                require_stack(1)
            elif opcode == 0xC4:
                widened_opcode = instruction[1]
                index = int.from_bytes(instruction[2:4], "big")
                slots = 2 if widened_opcode in {0x16, 0x18, 0x37, 0x39} else 1
                require_local(index, slots)
                if widened_opcode in range(0x15, 0x1A):
                    require_stack(slots)
                elif widened_opcode in range(0x36, 0x3B):
                    require_stack(slots)
            elif opcode == 0xC5:
                dimensions = instruction[3]
                require_stack(dimensions, 1)
    except (IndexError, TypeError, ValueError):
        return None
    return minimum_stack, minimum_locals


def _valid_operand_stack_flow(
    payload: bytes,
    *,
    instruction_offsets: set[int],
    constant_pool_tags: list[int],
    constant_pool_values: list[object],
    exception_handlers: list[
        tuple[int, int, int] | tuple[int, int, int, str]
    ],
    max_stack: int,
    max_locals: int,
    method_access_flags: int,
    method_name: bytes,
    method_descriptor: bytes,
    this_name: bytes,
    required_stack_map_offsets: set[int] | None = None,
    major_version: int = PINNED_JAVA_CLASS_MAJOR_VERSION,
    super_name: bytes | None = None,
    known_class_kinds: Mapping[bytes, bool] | None = None,
    known_superclasses: Mapping[bytes, bytes | None] | None = None,
    direct_interfaces: set[bytes] | None = None,
) -> (
    dict[int, tuple[tuple[str, ...], tuple[str, ...]]]
    | bool
):
    offsets = sorted(instruction_offsets)
    typed_exception_handlers = [
        (
            handler[0],
            handler[1],
            handler[2],
            handler[3] if len(handler) == 4 else "reference",
        )
        for handler in exception_handlers
    ]
    if (
        max_locals > MAX_OMITTED_CLASS_LOCALS
        or max_locals * len(offsets)
        > MAX_OMITTED_CLASS_STATE_CELLS
        or max_stack * len(offsets)
        > MAX_OMITTED_CLASS_STATE_CELLS
        or len(exception_handlers) * len(offsets)
        > MAX_OMITTED_CLASS_STATE_CELLS
    ):
        return False
    exception_handler_offsets = {
        handler_pc
        for _, _, handler_pc, _ in typed_exception_handlers
    }
    if required_stack_map_offsets is not None:
        required_stack_map_offsets.update(exception_handler_offsets)
    effects: dict[int, tuple[int, int]] = {}
    instructions: dict[int, bytes] = {}
    successors: dict[int, set[int]] = {}
    return_opcode = _method_descriptor_return_opcode(method_descriptor)
    if return_opcode is None:
        return False

    def constant(index: int) -> object | None:
        return (
            constant_pool_values[index]
            if 0 < index < len(constant_pool_values)
            else None
        )

    def referenced_descriptor(index: int) -> bytes | None:
        reference = constant(index)
        if not isinstance(reference, tuple) or len(reference) != 2:
            return None
        name_and_type = constant(reference[1])
        if not isinstance(name_and_type, tuple) or len(name_and_type) != 2:
            return None
        descriptor = constant(name_and_type[1])
        return descriptor if isinstance(descriptor, bytes) else None

    def referenced_name(index: int) -> bytes | None:
        reference = constant(index)
        if not isinstance(reference, tuple) or len(reference) != 2:
            return None
        name_and_type = constant(reference[1])
        if not isinstance(name_and_type, tuple) or len(name_and_type) != 2:
            return None
        name = constant(name_and_type[0])
        return name if isinstance(name, bytes) else None

    def class_reference(index: int) -> str | None:
        class_value = constant(index)
        if not isinstance(class_value, tuple) or len(class_value) != 1:
            return None
        name = constant(class_value[0])
        if not isinstance(name, bytes):
            return None
        descriptor = name if name.startswith(b"[") else b"L" + name + b";"
        return f"reference:{descriptor.decode('latin-1')}"

    try:
        for position, instruction_offset in enumerate(offsets):
            next_offset = (
                offsets[position + 1]
                if position + 1 < len(offsets)
                else None
            )
            end = next_offset if next_offset is not None else len(payload)
            instruction = payload[instruction_offset:end]
            instructions[instruction_offset] = instruction
            opcode = instruction[0]
            if opcode in range(0xAC, 0xB2) and opcode != return_opcode:
                return False
            popped = 0
            pushed = 0
            terminal = opcode in set(range(0xAC, 0xB2)) | {0xBF}
            targets: set[int] = set()

            if opcode in {0x09, 0x0A, 0x0E, 0x0F, 0x14}:
                pushed = 2
            elif opcode in (
                set(range(0x01, 0x09))
                | set(range(0x0B, 0x0E))
                | {0x10, 0x11, 0x12, 0x13, 0xBB}
            ):
                pushed = 1
            elif opcode in range(0x15, 0x1A):
                pushed = 2 if opcode in {0x16, 0x18} else 1
            elif opcode in range(0x1A, 0x2E):
                pushed = 2 if (opcode - 0x1A) // 4 in {1, 3} else 1
            elif opcode in range(0x2E, 0x36):
                popped = 2
                pushed = 2 if opcode in {0x2F, 0x31} else 1
            elif opcode in range(0x36, 0x3B):
                popped = 2 if opcode in {0x37, 0x39} else 1
            elif opcode in range(0x3B, 0x4F):
                popped = 2 if (opcode - 0x3B) // 4 in {1, 3} else 1
            elif opcode in range(0x4F, 0x57):
                popped = 4 if opcode in {0x50, 0x52} else 3
            elif opcode == 0x57:
                popped = 1
            elif opcode == 0x58:
                popped = 2
            elif opcode == 0x59:
                popped, pushed = 1, 2
            elif opcode == 0x5A:
                popped, pushed = 2, 3
            elif opcode == 0x5B:
                popped, pushed = 3, 4
            elif opcode == 0x5C:
                popped, pushed = 2, 4
            elif opcode == 0x5D:
                popped, pushed = 3, 5
            elif opcode == 0x5E:
                popped, pushed = 4, 6
            elif opcode == 0x5F:
                popped, pushed = 2, 2
            elif opcode in range(0x60, 0x74):
                category_two = opcode % 4 in {1, 3}
                popped, pushed = (4, 2) if category_two else (2, 1)
            elif opcode in range(0x74, 0x78):
                popped = pushed = 2 if opcode in {0x75, 0x77} else 1
            elif opcode in range(0x78, 0x7E):
                popped, pushed = (
                    (3, 2) if opcode in {0x79, 0x7B, 0x7D} else (2, 1)
                )
            elif opcode in range(0x7E, 0x84):
                category_two = opcode in {0x7F, 0x81, 0x83}
                popped, pushed = (4, 2) if category_two else (2, 1)
            elif opcode in {0x85, 0x87, 0x8C, 0x8D}:
                popped, pushed = 1, 2
            elif opcode in {0x86, 0x8B, 0x91, 0x92, 0x93}:
                popped = pushed = 1
            elif opcode in {0x88, 0x89, 0x8E, 0x90}:
                popped, pushed = 2, 1
            elif opcode in {0x8A, 0x8F}:
                popped = pushed = 2
            elif opcode == 0x94 or opcode in {0x97, 0x98}:
                popped, pushed = 4, 1
            elif opcode in {0x95, 0x96}:
                popped, pushed = 2, 1
            elif opcode in set(range(0x99, 0x9F)) | {0xC6, 0xC7}:
                popped = 1
                targets.add(
                    instruction_offset
                    + int.from_bytes(instruction[1:3], "big", signed=True)
                )
            elif opcode in range(0x9F, 0xA7):
                popped = 2
                targets.add(
                    instruction_offset
                    + int.from_bytes(instruction[1:3], "big", signed=True)
                )
            elif opcode in {0xA7, 0xA8, 0xC8, 0xC9}:
                if opcode in {0xA8, 0xC9} and major_version >= 51:
                    return False
                displacement_size = 2 if opcode in {0xA7, 0xA8} else 4
                targets.add(
                    instruction_offset
                    + int.from_bytes(
                        instruction[1 : 1 + displacement_size],
                        "big",
                        signed=True,
                    )
                )
                if opcode in {0xA8, 0xC9}:
                    pushed = 1
                terminal = True
            elif opcode == 0xA9 or (
                opcode == 0xC4 and instruction[1] == 0xA9
            ):
                if major_version >= 51:
                    return False
                terminal = True
            elif opcode in {0xAA, 0xAB}:
                popped = 1
                cursor = 1 + (4 - ((instruction_offset + 1) % 4)) % 4
                targets.add(
                    instruction_offset
                    + int.from_bytes(
                        instruction[cursor : cursor + 4],
                        "big",
                        signed=True,
                    )
                )
                cursor += 4
                if opcode == 0xAA:
                    low = int.from_bytes(
                        instruction[cursor : cursor + 4],
                        "big",
                        signed=True,
                    )
                    high = int.from_bytes(
                        instruction[cursor + 4 : cursor + 8],
                        "big",
                        signed=True,
                    )
                    cursor += 8
                    branch_count = high - low + 1
                    stride = 4
                else:
                    branch_count = int.from_bytes(
                        instruction[cursor : cursor + 4],
                        "big",
                        signed=True,
                    )
                    cursor += 4
                    stride = 8
                for branch in range(branch_count):
                    displacement_offset = cursor + branch * stride
                    if opcode == 0xAB:
                        displacement_offset += 4
                    targets.add(
                        instruction_offset
                        + int.from_bytes(
                            instruction[
                                displacement_offset : displacement_offset + 4
                            ],
                            "big",
                            signed=True,
                        )
                    )
                terminal = True
            elif opcode in {0xAC, 0xAE, 0xB0, 0xBF, 0xC2, 0xC3}:
                popped = 1
            elif opcode in {0xAD, 0xAF}:
                popped = 2
            elif opcode in range(0xB2, 0xB6):
                descriptor = referenced_descriptor(
                    int.from_bytes(instruction[1:3], "big")
                )
                parsed = (
                    _field_descriptor_end(descriptor)
                    if isinstance(descriptor, bytes)
                    else None
                )
                if parsed is None or parsed[0] != len(descriptor):
                    return False
                slots = parsed[1]
                if opcode == 0xB2:
                    pushed = slots
                elif opcode == 0xB3:
                    popped = slots
                elif opcode == 0xB4:
                    popped, pushed = 1, slots
                else:
                    popped = 1 + slots
            elif opcode in range(0xB6, 0xBB):
                descriptor = referenced_descriptor(
                    int.from_bytes(instruction[1:3], "big")
                )
                parameter_slots = (
                    _method_descriptor_parameter_slots(descriptor)
                    if isinstance(descriptor, bytes)
                    else None
                )
                return_slots = (
                    _method_descriptor_return_slots(descriptor)
                    if isinstance(descriptor, bytes)
                    else None
                )
                if parameter_slots is None or return_slots is None:
                    return False
                popped = parameter_slots + (
                    0 if opcode in {0xB8, 0xBA} else 1
                )
                pushed = return_slots
            elif opcode in {0xBC, 0xBD}:
                popped = pushed = 1
            elif opcode == 0xBE:
                popped = pushed = 1
            elif opcode in {0xC0, 0xC1}:
                popped = pushed = 1
            elif opcode == 0xC5:
                popped, pushed = instruction[3], 1
            elif opcode == 0xC4:
                widened_opcode = instruction[1]
                if widened_opcode in range(0x15, 0x1A):
                    pushed = 2 if widened_opcode in {0x16, 0x18} else 1
                elif widened_opcode in range(0x36, 0x3B):
                    popped = 2 if widened_opcode in {0x37, 0x39} else 1

            if required_stack_map_offsets is not None:
                required_stack_map_offsets.update(
                    target for target in targets if target != 0
                )
            if not terminal:
                if next_offset is None:
                    return False
                targets.add(next_offset)
            effects[instruction_offset] = (popped, pushed)
            successors[instruction_offset] = targets
    except (IndexError, TypeError, ValueError):
        return False

    def consume(
        stack: tuple[str, ...],
        expected: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        if len(stack) < len(expected):
            return None
        actual = stack[len(stack) - len(expected) :] if expected else ()

        def reference_name(slot: str) -> bytes | None:
            descriptor = slot.removeprefix("reference:")
            if (
                descriptor.startswith("L")
                and descriptor.endswith(";")
            ):
                return descriptor[1:-1].encode("latin-1")
            return None

        def known_reference_assignable(
            actual_slot: str,
            expected_slot: str,
        ) -> bool:
            actual_name = reference_name(actual_slot)
            expected_name = reference_name(expected_slot)
            if (
                actual_name == b"java/lang/Object"
                and expected_name != b"java/lang/Object"
            ):
                return False
            if expected_name == b"java/lang/Throwable":
                if (
                    known_class_kinds is not None
                    and known_class_kinds.get(actual_name) is True
                ):
                    return False
                if (
                    actual_name is None
                    or known_superclasses is None
                    or actual_name not in known_superclasses
                ):
                    return True
                current: bytes | None = actual_name
                visited: set[bytes] = set()
                while current is not None and current not in visited:
                    if current == expected_name:
                        return True
                    if current == b"java/lang/Object":
                        return False
                    visited.add(current)
                    if current not in known_superclasses:
                        return True
                    current = known_superclasses[current]
                return False
            if (
                actual_name is None
                or expected_name is None
                or known_class_kinds is None
                or known_superclasses is None
                or expected_name not in known_class_kinds
                or known_class_kinds.get(expected_name) is True
                or actual_name not in known_superclasses
            ):
                return True
            current: bytes | None = actual_name
            visited: set[bytes] = set()
            while (
                current is not None
                and current in known_superclasses
                and current not in visited
            ):
                if current == expected_name:
                    return True
                visited.add(current)
                current = known_superclasses[current]
            return current == expected_name

        def assignable(actual_slot: str, expected_slot: str) -> bool:
            if expected_slot == "array_reference":
                return (
                    actual_slot == "null"
                    or actual_slot.startswith("reference:[")
                )
            if (
                actual_slot == expected_slot
                or actual_slot == "unknown"
                or expected_slot == "unknown"
            ):
                return True
            if expected_slot == "reference":
                return (
                    actual_slot == "null"
                    or actual_slot == "merged_reference"
                    or actual_slot.startswith("reference:")
                )
            if expected_slot.startswith("reference:"):
                if actual_slot == "null":
                    return True
                if actual_slot == "merged_reference":
                    return True
                if not actual_slot.startswith("reference:"):
                    return False
                return (
                    actual_slot == expected_slot
                    or expected_slot
                    == "reference:Ljava/lang/Object;"
                    or known_reference_assignable(
                        actual_slot,
                        expected_slot,
                    )
                )
            return False
        if any(
            not assignable(actual_slot, expected_slot)
            for actual_slot, expected_slot in zip(actual, expected)
        ):
            return None
        return stack[: len(stack) - len(expected)] if expected else stack

    def descriptor_slots(index: int) -> tuple[str, ...] | None:
        descriptor = referenced_descriptor(index)
        return (
            _field_descriptor_stack_slots(descriptor)
            if isinstance(descriptor, bytes)
            else None
        )

    method_signature = _method_descriptor_stack_slots(method_descriptor)
    if method_signature is None:
        return False
    parameter_types, return_types = method_signature
    initial_local_types = (
        (
            ()
            if method_access_flags & 0x0008
            else (
                "uninitialized_this"
                if method_name == b"<init>"
                else f"reference:L{this_name.decode('latin-1')};",
            )
        )
        + parameter_types
    )
    if len(initial_local_types) > max_locals:
        return False
    initial_locals = initial_local_types + ("uninitialized",) * (
        max_locals - len(initial_local_types)
    )

    def category_one(slot: str) -> bool:
        return slot not in {"long", "double"}

    def category_two(slots: tuple[str, ...]) -> bool:
        return (
            len(slots) == 2
            and slots[0] == slots[1]
            and slots[0] in {"long", "double"}
        )

    def valid_stack_manipulation(
        opcode: int,
        stack: tuple[str, ...],
    ) -> bool:
        if opcode in {0x57, 0x59}:
            return bool(stack) and category_one(stack[-1])
        if opcode in {0x58, 0x5C}:
            return len(stack) >= 2 and (
                category_two(stack[-2:])
                or all(category_one(slot) for slot in stack[-2:])
            )
        if opcode in {0x5A, 0x5F}:
            return len(stack) >= 2 and all(
                category_one(slot) for slot in stack[-2:]
            )
        if opcode == 0x5B:
            return len(stack) >= 3 and (
                all(category_one(slot) for slot in stack[-3:])
                or (
                    category_two(stack[-3:-1])
                    and category_one(stack[-1])
                )
            )
        if opcode == 0x5D:
            return len(stack) >= 3 and (
                all(category_one(slot) for slot in stack[-3:])
                or (
                    category_one(stack[-3])
                    and category_two(stack[-2:])
                )
            )
        if opcode == 0x5E:
            if len(stack) < 4:
                return False
            values = stack[-4:]
            return (
                all(category_one(slot) for slot in values)
                or (
                    category_two(values[:2])
                    and all(category_one(slot) for slot in values[2:])
                )
                or (
                    all(category_one(slot) for slot in values[:2])
                    and category_two(values[2:])
                )
                or (
                    category_two(values[:2])
                    and category_two(values[2:])
                )
            )
        return True

    def typed_transition(
        instruction_offset: int,
        stack: tuple[str, ...],
        locals_state: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        instruction = instructions[instruction_offset]
        opcode = instruction[0]
        expected: tuple[str, ...] = ()
        pushed_types: tuple[str, ...] = ()
        local_read: tuple[int, tuple[str, ...]] | None = None
        local_write: tuple[int, tuple[str, ...]] | None = None
        local_increment: int | None = None
        initializes_receiver = False
        initialized_token: str | None = None
        initialized_reference: str | None = None

        def valid_array_operand(slot: str, array_opcode: int) -> bool:
            if slot == "null":
                return True
            if not slot.startswith("reference:["):
                return False
            descriptor = slot.removeprefix("reference:")
            element_index = (
                array_opcode - 0x2E
                if array_opcode < 0x4F
                else array_opcode - 0x4F
            )
            if element_index == 4:
                return descriptor.startswith(("[L", "[["))
            expected_descriptors = (
                {"[I"},
                {"[J"},
                {"[F"},
                {"[D"},
                set(),
                {"[B", "[Z"},
                {"[C"},
                {"[S"},
            )
            return descriptor in expected_descriptors[element_index]

        if opcode == 0x01:
            pushed_types = ("null",)
        elif opcode in set(range(0x02, 0x09)) | {0x10, 0x11}:
            pushed_types = ("int",)
        elif opcode in {0x09, 0x0A}:
            pushed_types = ("long", "long")
        elif opcode in range(0x0B, 0x0E):
            pushed_types = ("float",)
        elif opcode in {0x0E, 0x0F}:
            pushed_types = ("double", "double")
        elif opcode in {0x12, 0x13, 0x14}:
            pool_index = (
                instruction[1]
                if opcode == 0x12
                else int.from_bytes(instruction[1:3], "big")
            )
            tag = constant_pool_tags[pool_index]
            if tag == 3:
                pushed_types = ("int",)
            elif tag == 4:
                pushed_types = ("float",)
            elif tag == 5:
                pushed_types = ("long", "long")
            elif tag == 6:
                pushed_types = ("double", "double")
            elif tag == 17:
                dynamic_slots = descriptor_slots(pool_index)
                if dynamic_slots is None:
                    return None
                pushed_types = dynamic_slots
            elif tag == 8:
                pushed_types = ("reference:Ljava/lang/String;",)
            elif tag == 7:
                pushed_types = ("reference:Ljava/lang/Class;",)
            elif tag == 15:
                pushed_types = (
                    "reference:Ljava/lang/invoke/MethodHandle;",
                )
            elif tag == 16:
                pushed_types = (
                    "reference:Ljava/lang/invoke/MethodType;",
                )
            else:
                pushed_types = ("reference",)
        elif opcode in range(0x15, 0x1A):
            pushed_types = {
                0x15: ("int",),
                0x16: ("long", "long"),
                0x17: ("float",),
                0x18: ("double", "double"),
                0x19: ("reference",),
            }[opcode]
            local_read = instruction[1], pushed_types
        elif opcode in range(0x1A, 0x2E):
            pushed_types = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
                ("reference",),
            )[(opcode - 0x1A) // 4]
            local_read = (opcode - 0x1A) % 4, pushed_types
        elif opcode in range(0x2E, 0x36):
            element_types = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
                ("reference",),
                ("int",),
                ("int",),
                ("int",),
            )
            if (
                len(stack) < 2
                or not valid_array_operand(stack[-2], opcode)
            ):
                return None
            expected = ("reference", "int")
            pushed_types = element_types[opcode - 0x2E]
            if (
                opcode == 0x32
                and len(stack) >= 2
                and stack[-2].startswith("reference:[")
            ):
                array_descriptor = stack[-2].removeprefix("reference:")
                component = array_descriptor[1:]
                pushed_types = (
                    component
                    if component.startswith("reference:")
                    else f"reference:{component}",
                )
        elif opcode in range(0x36, 0x3B):
            expected = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
                ("reference",),
            )[opcode - 0x36]
            local_write = instruction[1], expected
        elif opcode in range(0x3B, 0x4F):
            expected = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
                ("reference",),
            )[(opcode - 0x3B) // 4]
            local_write = (opcode - 0x3B) % 4, expected
        elif opcode in range(0x4F, 0x57):
            element_types = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
                ("reference",),
                ("int",),
                ("int",),
                ("int",),
            )
            element_type = element_types[opcode - 0x4F]
            if (
                len(stack) < 2 + len(element_type)
                or not valid_array_operand(
                    stack[-2 - len(element_type)],
                    opcode,
                )
            ):
                return None
            expected = (
                ("reference", "int")
                + element_type
            )
        elif opcode == 0x57:
            if not valid_stack_manipulation(opcode, stack):
                return None
            expected = ("unknown",)
        elif opcode == 0x58:
            if not valid_stack_manipulation(opcode, stack):
                return None
            expected = ("unknown", "unknown")
        elif opcode in range(0x59, 0x5F):
            popped, _ = effects[instruction_offset]
            if (
                len(stack) < popped
                or not valid_stack_manipulation(opcode, stack)
            ):
                return None
            if opcode == 0x59:
                return stack + stack[-1:], locals_state
            if opcode == 0x5A:
                return (
                    stack[:-2] + stack[-1:] + stack[-2:],
                    locals_state,
                )
            if opcode == 0x5B:
                return (
                    stack[:-3] + stack[-1:] + stack[-3:],
                    locals_state,
                )
            if opcode == 0x5C:
                return stack + stack[-2:], locals_state
            if opcode == 0x5D:
                return (
                    stack[:-3] + stack[-2:] + stack[-3:],
                    locals_state,
                )
            return stack[:-4] + stack[-2:] + stack[-4:], locals_state
        elif opcode == 0x5F:
            if not valid_stack_manipulation(opcode, stack):
                return None
            return (
                stack[:-2] + (stack[-1], stack[-2]),
                locals_state,
            )
        elif opcode in range(0x60, 0x74):
            value_type = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
            )[opcode % 4]
            expected = value_type + value_type
            pushed_types = value_type
        elif opcode in range(0x74, 0x78):
            expected = pushed_types = (
                ("int",),
                ("long", "long"),
                ("float",),
                ("double", "double"),
            )[opcode - 0x74]
        elif opcode in range(0x78, 0x7E):
            value_type = (
                ("long", "long")
                if opcode in {0x79, 0x7B, 0x7D}
                else ("int",)
            )
            expected = value_type + ("int",)
            pushed_types = value_type
        elif opcode in range(0x7E, 0x84):
            value_type = (
                ("long", "long")
                if opcode in {0x7F, 0x81, 0x83}
                else ("int",)
            )
            expected = value_type + value_type
            pushed_types = value_type
        elif opcode == 0x84:
            local_increment = instruction[1]
        elif opcode in range(0x85, 0x94):
            conversions = {
                0x85: (("int",), ("long", "long")),
                0x86: (("int",), ("float",)),
                0x87: (("int",), ("double", "double")),
                0x88: (("long", "long"), ("int",)),
                0x89: (("long", "long"), ("float",)),
                0x8A: (("long", "long"), ("double", "double")),
                0x8B: (("float",), ("int",)),
                0x8C: (("float",), ("long", "long")),
                0x8D: (("float",), ("double", "double")),
                0x8E: (("double", "double"), ("int",)),
                0x8F: (("double", "double"), ("long", "long")),
                0x90: (("double", "double"), ("float",)),
                0x91: (("int",), ("int",)),
                0x92: (("int",), ("int",)),
                0x93: (("int",), ("int",)),
            }
            expected, pushed_types = conversions[opcode]
        elif opcode == 0x94:
            expected = ("long", "long", "long", "long")
            pushed_types = ("int",)
        elif opcode in {0x95, 0x96}:
            expected = ("float", "float")
            pushed_types = ("int",)
        elif opcode in {0x97, 0x98}:
            expected = ("double", "double", "double", "double")
            pushed_types = ("int",)
        elif opcode in set(range(0x99, 0xA5)) | {0xAA, 0xAB}:
            expected = (
                ("int", "int")
                if opcode in range(0x9F, 0xA5)
                else ("int",)
            )
        elif opcode in {0xA5, 0xA6}:
            expected = ("reference", "reference")
        elif opcode in {0xA8, 0xC9}:
            pushed_types = (
                f"return_address:{instruction_offset + len(instruction)}",
            )
        elif opcode == 0xA9:
            local_index = instruction[1]
            if (
                local_index >= len(locals_state)
                or not locals_state[local_index].startswith(
                    "return_address:"
                )
            ):
                return None
        elif opcode in {0xAC, 0xAE}:
            expected = (("int",) if opcode == 0xAC else ("float",))
        elif opcode in {0xAD, 0xAF}:
            expected = (
                ("long", "long")
                if opcode == 0xAD
                else ("double", "double")
            )
        elif opcode == 0xB0:
            expected = return_types
        elif opcode in range(0xB2, 0xB6):
            field_index = int.from_bytes(instruction[1:3], "big")
            field_slots = descriptor_slots(field_index)
            if field_slots is None:
                return None
            field_reference = constant(field_index)
            owner_reference = (
                class_reference(field_reference[0])
                if isinstance(field_reference, tuple)
                else None
            )
            if owner_reference is None:
                return None
            if opcode == 0xB2:
                pushed_types = field_slots
            elif opcode == 0xB3:
                expected = field_slots
            elif opcode == 0xB4:
                expected = (owner_reference,)
                pushed_types = field_slots
            else:
                receiver_type = owner_reference
                if (
                    method_name == b"<init>"
                    and len(stack) >= len(field_slots) + 1
                    and stack[-len(field_slots) - 1]
                    == "uninitialized_this"
                ):
                    if owner_reference == (
                        f"reference:L{this_name.decode('latin-1')};"
                    ):
                        receiver_type = "uninitialized_this"
                expected = (receiver_type,) + field_slots
        elif opcode in range(0xB6, 0xBB):
            pool_index = int.from_bytes(instruction[1:3], "big")
            descriptor = referenced_descriptor(pool_index)
            invocation_name = referenced_name(pool_index)
            if invocation_name == b"<init>" and opcode != 0xB7:
                return None
            signature = (
                _method_descriptor_stack_slots(descriptor)
                if isinstance(descriptor, bytes)
                else None
            )
            if signature is None:
                return None
            parameters, invocation_return_types = signature
            receiver_type = "reference"
            reference = constant(pool_index)
            owner_reference = (
                class_reference(reference[0])
                if isinstance(reference, tuple)
                else None
            )
            if opcode in {0xB6, 0xB9}:
                if owner_reference is None:
                    return None
                receiver_type = owner_reference
            if opcode == 0xB7 and owner_reference is None:
                return None
            if opcode == 0xB7 and invocation_name != b"<init>":
                allowed_owner_names = {
                    this_name,
                    *(direct_interfaces or set()),
                }
                current_super = super_name
                visited_supers: set[bytes] = set()
                while (
                    current_super is not None
                    and current_super not in visited_supers
                ):
                    allowed_owner_names.add(current_super)
                    visited_supers.add(current_super)
                    if (
                        known_superclasses is None
                        or current_super not in known_superclasses
                    ):
                        break
                    current_super = known_superclasses[current_super]
                allowed_owner_references = {
                    f"reference:L{name.decode('latin-1')};"
                    for name in allowed_owner_names
                }
                if owner_reference not in allowed_owner_references:
                    return None
                receiver_type = (
                    f"reference:L{this_name.decode('latin-1')};"
                )
            if (
                opcode == 0xB7
                and invocation_name == b"<init>"
                and len(stack) >= len(parameters) + 1
            ):
                candidate_receiver = stack[-len(parameters) - 1]
                if candidate_receiver == "uninitialized_this":
                    allowed_owners = {
                        f"reference:L{this_name.decode('latin-1')};",
                    }
                    if super_name is not None:
                        allowed_owners.add(
                            "reference:"
                            f"L{super_name.decode('latin-1')};"
                        )
                    if owner_reference not in allowed_owners:
                        return None
                    receiver_type = candidate_receiver
                    initializes_receiver = True
                    initialized_token = candidate_receiver
                    initialized_reference = (
                        f"reference:L{this_name.decode('latin-1')};"
                    )
                elif candidate_receiver.startswith("uninitialized:"):
                    allocation_reference = candidate_receiver.split(
                        ":", 2
                    )[2]
                    if owner_reference != allocation_reference:
                        return None
                    receiver_type = candidate_receiver
                    initializes_receiver = True
                    initialized_token = candidate_receiver
                    initialized_reference = allocation_reference
                else:
                    return None
            expected = (
                ()
                if opcode in {0xB8, 0xBA}
                else (receiver_type,)
            ) + parameters
            pushed_types = invocation_return_types
        elif opcode == 0xBB:
            allocation_reference = class_reference(
                int.from_bytes(instruction[1:3], "big")
            )
            if (
                allocation_reference is None
                or allocation_reference.startswith("reference:[")
            ):
                return None
            pushed_types = (
                f"uninitialized:{instruction_offset}:"
                f"{allocation_reference}",
            )
        elif opcode in {0xBC, 0xBD}:
            expected = ("int",)
            if opcode == 0xBC:
                primitive = {
                    4: "Z",
                    5: "C",
                    6: "F",
                    7: "D",
                    8: "B",
                    9: "S",
                    10: "I",
                    11: "J",
                }[instruction[1]]
                pushed_types = (f"reference:[{primitive}",)
            else:
                component_reference = class_reference(
                    int.from_bytes(instruction[1:3], "big")
                )
                if component_reference is None:
                    return None
                component = component_reference.removeprefix("reference:")
                pushed_types = (f"reference:[{component}",)
        elif opcode == 0xBE:
            expected = ("array_reference",)
            pushed_types = ("int",)
        elif opcode == 0xBF:
            expected = ("reference:Ljava/lang/Throwable;",)
        elif opcode == 0xC0:
            cast_reference = class_reference(
                int.from_bytes(instruction[1:3], "big")
            )
            if cast_reference is None:
                return None
            expected = ("reference",)
            pushed_types = (cast_reference,)
        elif opcode == 0xC1:
            expected = ("reference",)
            pushed_types = ("int",)
        elif opcode in {0xC2, 0xC3, 0xC6, 0xC7}:
            expected = ("reference",)
        elif opcode == 0xC5:
            expected = ("int",) * instruction[3]
            array_reference = class_reference(
                int.from_bytes(instruction[1:3], "big")
            )
            if array_reference is None:
                return None
            pushed_types = (array_reference,)
        elif opcode == 0xC4:
            widened_opcode = instruction[1]
            local_index = int.from_bytes(instruction[2:4], "big")
            if widened_opcode in range(0x15, 0x1A):
                pushed_types = (
                    ("int",),
                    ("long", "long"),
                    ("float",),
                    ("double", "double"),
                    ("reference",),
                )[widened_opcode - 0x15]
                local_read = local_index, pushed_types
            elif widened_opcode in range(0x36, 0x3B):
                expected = (
                    ("int",),
                    ("long", "long"),
                    ("float",),
                    ("double", "double"),
                    ("reference",),
                )[widened_opcode - 0x36]
                local_write = local_index, expected
            elif widened_opcode == 0x84:
                local_increment = local_index
            elif widened_opcode == 0xA9:
                if (
                    local_index >= len(locals_state)
                    or not locals_state[local_index].startswith(
                        "return_address:"
                    )
                ):
                    return None

        if local_read is not None:
            local_index, local_types = local_read
            actual_local_types = locals_state[
                local_index : local_index + len(local_types)
            ]
            if (
                local_index + len(local_types) > len(locals_state)
                or (
                    actual_local_types != local_types
                    and not (
                        local_types == ("reference",)
                        and actual_local_types
                        and (
                            actual_local_types[0] == "null"
                            or actual_local_types[0]
                            == "merged_reference"
                            or actual_local_types[0]
                            .startswith("reference:")
                            or actual_local_types[0]
                            == "uninitialized_this"
                            or actual_local_types[0]
                            .startswith("uninitialized:")
                        )
                    )
                )
            ):
                return None
            if (
                local_types == ("reference",)
                and actual_local_types
            ):
                pushed_types = actual_local_types
        if (
            local_increment is not None
            and (
                local_increment >= len(locals_state)
                or locals_state[local_increment] != "int"
            )
        ):
            return None
        if (
            local_write is not None
            and local_write[1] == ("reference",)
        ):
            if not stack:
                return None
            stored_reference = stack[-1]
            if not (
                stored_reference == "reference"
                or stored_reference == "null"
                or stored_reference == "merged_reference"
                or stored_reference.startswith("reference:")
                or stored_reference.startswith("return_address:")
                or stored_reference == "uninitialized_this"
                or stored_reference.startswith("uninitialized:")
            ):
                return None
            expected = (stored_reference,)
            local_write = (local_write[0], expected)
        remaining = consume(stack, expected)
        if remaining is None:
            return None
        next_locals = locals_state
        if local_write is not None:
            local_index, local_types = local_write
            if local_index + len(local_types) > len(locals_state):
                return None
            local_values = list(locals_state)
            local_end = local_index + len(local_types)
            if (
                local_index > 0
                and local_values[local_index - 1]
                in {"long", "double"}
                and local_values[local_index - 1]
                == local_values[local_index]
            ):
                local_values[local_index - 1] = "uninitialized"
            if (
                local_end < len(local_values)
                and local_values[local_end - 1]
                in {"long", "double"}
                and local_values[local_end - 1]
                == local_values[local_end]
            ):
                local_values[local_end] = "uninitialized"
            local_values[
                local_index:local_end
            ] = local_types
            next_locals = tuple(local_values)
        result = remaining + pushed_types
        if initializes_receiver:
            if initialized_token is None or initialized_reference is None:
                return None
            result = tuple(
                initialized_reference
                if slot == initialized_token
                else slot
                for slot in result
            )
            next_locals = tuple(
                initialized_reference
                if slot == initialized_token
                else slot
                for slot in next_locals
            )
        if opcode == 0xBF:
            result = ()
        if (
            method_name == b"<init>"
            and opcode in range(0xAC, 0xB2)
            and "uninitialized_this" in next_locals
        ):
            return None
        if opcode in set(range(0xAC, 0xB2)) | {0xBF} and result:
            return None
        return result, next_locals

    depths = {0: 0}
    pending = [0]
    for handler_offset in exception_handler_offsets:
        if handler_offset in depths and depths[handler_offset] != 1:
            return False
        if handler_offset not in depths:
            depths[handler_offset] = 1
            pending.append(handler_offset)
    while pending:
        instruction_offset = pending.pop()
        depth = depths[instruction_offset]
        popped, pushed = effects[instruction_offset]
        if depth < popped:
            return False
        next_depth = depth - popped + pushed
        if depth > max_stack or next_depth > max_stack:
            return False
        for target in successors[instruction_offset]:
            if target in depths:
                if depths[target] != next_depth:
                    return False
                continue
            depths[target] = next_depth
            pending.append(target)

    exceptional_successors: dict[int, set[tuple[int, str]]] = {
        instruction_offset: {
            (handler_pc, catch_slot)
            for start_pc, end_pc, handler_pc, catch_slot
            in typed_exception_handlers
            if start_pc <= instruction_offset < end_pc
        }
        for instruction_offset in offsets
    }
    typed_states: dict[
        int,
        tuple[tuple[str, ...], tuple[str, ...]],
    ] = {0: ((), initial_locals)}
    typed_pending = [0]

    def merge_types(
        current: tuple[str, ...],
        incoming: tuple[str, ...],
        *,
        locals_state: bool = False,
    ) -> tuple[str, ...] | None:
        if len(current) != len(incoming):
            return None
        merged_values: list[str] = []
        for current_slot, incoming_slot in zip(current, incoming):
            if current_slot == incoming_slot:
                merged_values.append(current_slot)
            elif current_slot == "unknown":
                merged_values.append(incoming_slot)
            elif incoming_slot == "unknown":
                merged_values.append(current_slot)
            elif locals_state and (
                current_slot == "uninitialized"
                or incoming_slot == "uninitialized"
            ):
                merged_values.append("uninitialized")
            elif current_slot == "null" and incoming_slot.startswith(
                "reference:"
            ):
                merged_values.append(incoming_slot)
            elif incoming_slot == "null" and current_slot.startswith(
                "reference:"
            ):
                merged_values.append(current_slot)
            elif (
                current_slot == "merged_reference"
                and (
                    incoming_slot == "null"
                    or incoming_slot.startswith("reference:")
                )
            ) or (
                incoming_slot == "merged_reference"
                and (
                    current_slot == "null"
                    or current_slot.startswith("reference:")
                )
            ):
                merged_values.append("merged_reference")
            elif current_slot.startswith(
                "reference:"
            ) and incoming_slot.startswith("reference:"):
                merged_values.append("merged_reference")
            elif current_slot.startswith(
                "return_address:"
            ) and incoming_slot.startswith("return_address:"):
                return_offsets = sorted(
                    {
                        *current_slot.removeprefix(
                            "return_address:"
                        ).split(","),
                        *incoming_slot.removeprefix(
                            "return_address:"
                        ).split(","),
                    },
                    key=int,
                )
                merged_values.append(
                    "return_address:" + ",".join(return_offsets)
                )
            else:
                merged_values.append("invalid")
        merged = tuple(merged_values)
        return None if "invalid" in merged else merged

    def merge_state(
        target: int,
        incoming_stack: tuple[str, ...],
        incoming_locals: tuple[str, ...],
    ) -> bool | None:
        if target not in typed_states:
            typed_states[target] = (incoming_stack, incoming_locals)
            typed_pending.append(target)
            return True
        current_stack, current_locals = typed_states[target]
        merged_stack = merge_types(current_stack, incoming_stack)
        merged_locals = merge_types(
            current_locals,
            incoming_locals,
            locals_state=True,
        )
        if merged_stack is None or merged_locals is None:
            return None
        merged_state = merged_stack, merged_locals
        if merged_state != typed_states[target]:
            typed_states[target] = merged_state
            typed_pending.append(target)
        return True

    while typed_pending:
        instruction_offset = typed_pending.pop()
        current_stack, current_locals = typed_states[instruction_offset]
        next_state = typed_transition(
            instruction_offset,
            current_stack,
            current_locals,
        )
        if next_state is None:
            return False
        next_stack, next_locals = next_state
        instruction = instructions[instruction_offset]
        opcode = instruction[0]
        if opcode == 0xA9 or (
            opcode == 0xC4 and instruction[1] == 0xA9
        ):
            local_index = (
                instruction[1]
                if opcode == 0xA9
                else int.from_bytes(instruction[2:4], "big")
            )
            return_address = current_locals[local_index]
            transition_targets = {
                int(target)
                for target in return_address.removeprefix(
                    "return_address:"
                ).split(",")
            }
        else:
            transition_targets = successors[instruction_offset]
        for target in transition_targets:
            if merge_state(target, next_stack, next_locals) is None:
                return False
        for handler_offset, catch_slot in exceptional_successors[
            instruction_offset
        ]:
            if (
                merge_state(
                    handler_offset,
                    (catch_slot,),
                    current_locals,
                )
                is None
            ):
                return False
    return typed_states


def _valid_stack_map_table(
    payload: bytes,
    *,
    code: bytes,
    constant_pool_tags: list[int],
    constant_pool_values: list[object],
    instruction_offsets: set[int],
    computed_states: dict[
        int,
        tuple[tuple[str, ...], tuple[str, ...]],
    ],
    required_frame_offsets: set[int] | None = None,
    known_class_kinds: Mapping[bytes, bool] | None = None,
    known_superclasses: Mapping[bytes, bytes | None] | None = None,
) -> bool:
    offset = 0

    def read_uint(size: int) -> int:
        nonlocal offset
        end = offset + size
        if end > len(payload):
            raise ValueError("truncated StackMapTable")
        value = int.from_bytes(payload[offset:end], "big")
        offset = end
        return value

    def class_reference(index: int) -> str | None:
        if (
            not 0 < index < len(constant_pool_tags)
            or constant_pool_tags[index] != 7
        ):
            return None
        class_value = constant_pool_values[index]
        if not isinstance(class_value, tuple) or len(class_value) != 1:
            return None
        name_index = class_value[0]
        if (
            not isinstance(name_index, int)
            or not 0 < name_index < len(constant_pool_values)
        ):
            return None
        name = constant_pool_values[name_index]
        if not isinstance(name, bytes):
            return None
        descriptor = name if name.startswith(b"[") else b"L" + name + b";"
        return f"reference:{descriptor.decode('latin-1')}"

    def read_verification_type() -> tuple[str, ...] | None:
        tag = read_uint(1)
        if tag == 0:
            return ("uninitialized",)
        if tag == 1:
            return ("int",)
        if tag == 2:
            return ("float",)
        if tag == 3:
            return ("double", "double")
        if tag == 4:
            return ("long", "long")
        if tag == 5:
            return ("null",)
        if tag == 6:
            return ("uninitialized_this",)
        if tag == 7:
            pool_index = read_uint(2)
            reference = class_reference(pool_index)
            return (reference,) if reference is not None else None
        if tag == 8:
            code_offset = read_uint(2)
            if (
                code_offset not in instruction_offsets
                or code[code_offset] != 0xBB
                or code_offset + 3 > len(code)
            ):
                return None
            reference = class_reference(
                int.from_bytes(code[code_offset + 1 : code_offset + 3], "big")
            )
            return (
                (
                    f"uninitialized:{code_offset}:{reference}",
                )
                if reference is not None
                else None
            )
        return None

    def stack_slots_to_entries(
        slots: tuple[str, ...],
    ) -> list[tuple[str, ...]] | None:
        trimmed = list(slots)
        while trimmed and trimmed[-1] == "uninitialized":
            trimmed.pop()
        entries: list[tuple[str, ...]] = []
        slot_offset = 0
        while slot_offset < len(trimmed):
            slot = trimmed[slot_offset]
            if slot in {"long", "double"}:
                if (
                    slot_offset + 1 >= len(trimmed)
                    or trimmed[slot_offset + 1] != slot
                ):
                    return None
                entries.append((slot, slot))
                slot_offset += 2
            else:
                entries.append((slot,))
                slot_offset += 1
        return entries

    def flatten(entries: list[tuple[str, ...]]) -> tuple[str, ...]:
        return tuple(slot for entry in entries for slot in entry)

    def reference_assignable(
        computed_slot: str,
        declared_slot: str,
    ) -> bool:
        if computed_slot == "null":
            return declared_slot.startswith("reference:")
        if not (
            computed_slot.startswith("reference:")
            and declared_slot.startswith("reference:")
        ):
            return False
        if declared_slot == "reference:Ljava/lang/Object;":
            return True

        def class_name(slot: str) -> bytes | None:
            descriptor = slot.removeprefix("reference:")
            if descriptor.startswith("L") and descriptor.endswith(";"):
                return descriptor[1:-1].encode("latin-1")
            return None

        computed_name = class_name(computed_slot)
        declared_name = class_name(declared_slot)
        if (
            computed_name is None
            or declared_name is None
            or known_class_kinds is None
            or known_superclasses is None
            or declared_name not in known_class_kinds
            or known_class_kinds.get(declared_name) is True
            or computed_name not in known_superclasses
        ):
            return True
        current: bytes | None = computed_name
        visited: set[bytes] = set()
        while (
            current is not None
            and current in known_superclasses
            and current not in visited
        ):
            if current == declared_name:
                return True
            visited.add(current)
            current = known_superclasses[current]
        return current == declared_name

    def states_match(
        declared: tuple[str, ...],
        computed: tuple[str, ...],
        *,
        locals_state: bool = False,
    ) -> bool:
        if len(declared) != len(computed):
            return False
        return all(
            declared_slot == computed_slot
            or reference_assignable(computed_slot, declared_slot)
            or (
                computed_slot == "merged_reference"
                and declared_slot.startswith("reference:")
            )
            or (
                locals_state
                and declared_slot == "uninitialized"
            )
            or (
                computed_slot == "reference"
                and (
                    declared_slot == "null"
                    or declared_slot.startswith("reference:")
                )
            )
            for declared_slot, computed_slot in zip(declared, computed)
        )

    try:
        initial_state = computed_states.get(0)
        if initial_state is None:
            return False
        frame_locals = stack_slots_to_entries(initial_state[1])
        if frame_locals is None:
            return False
        previous_frame_offset = -1
        declared_frame_offsets: set[int] = set()
        for _ in range(read_uint(2)):
            frame_type = read_uint(1)
            frame_stack: list[tuple[str, ...]] = []
            if frame_type <= 63:
                offset_delta = frame_type
            elif frame_type <= 127:
                offset_delta = frame_type - 64
                stack_type = read_verification_type()
                if stack_type is None:
                    return False
                frame_stack.append(stack_type)
            elif frame_type == 247:
                offset_delta = read_uint(2)
                stack_type = read_verification_type()
                if stack_type is None:
                    return False
                frame_stack.append(stack_type)
            elif 248 <= frame_type <= 251:
                offset_delta = read_uint(2)
                if frame_type < 251:
                    chopped = 251 - frame_type
                    if chopped > len(frame_locals):
                        return False
                    frame_locals = frame_locals[:-chopped]
            elif 252 <= frame_type <= 254:
                offset_delta = read_uint(2)
                for _ in range(frame_type - 251):
                    local_type = read_verification_type()
                    if local_type is None:
                        return False
                    frame_locals.append(local_type)
            elif frame_type == 255:
                offset_delta = read_uint(2)
                frame_locals = []
                for _ in range(read_uint(2)):
                    local_type = read_verification_type()
                    if local_type is None:
                        return False
                    frame_locals.append(local_type)
                for _ in range(read_uint(2)):
                    stack_type = read_verification_type()
                    if stack_type is None:
                        return False
                    frame_stack.append(stack_type)
            else:
                return False
            frame_offset = (
                offset_delta
                if previous_frame_offset < 0
                else previous_frame_offset + offset_delta + 1
            )
            if (
                frame_offset not in instruction_offsets
                or frame_offset >= len(code)
            ):
                return False
            computed_state = computed_states.get(frame_offset)
            if computed_state is None:
                return False
            computed_stack, computed_locals = computed_state
            declared_locals = flatten(frame_locals)
            if len(declared_locals) > len(computed_locals):
                return False
            declared_locals += ("uninitialized",) * (
                len(computed_locals) - len(declared_locals)
            )
            if not (
                states_match(flatten(frame_stack), computed_stack)
                and states_match(
                    declared_locals,
                    computed_locals,
                    locals_state=True,
                )
            ):
                return False
            declared_frame_offsets.add(frame_offset)
            previous_frame_offset = frame_offset
        return (
            offset == len(payload)
            and (
                required_frame_offsets is None
                or required_frame_offsets <= declared_frame_offsets
            )
        )
    except ValueError:
        return False


def _valid_class_file(
    payload: bytes,
    *,
    expected_name: bytes | None = None,
    known_class_kinds: Mapping[bytes, bool] | None = None,
    known_superclasses: Mapping[bytes, bytes | None] | None = None,
) -> bool:
    offset = 0

    def read(size: int) -> bytes:
        nonlocal offset
        end = offset + size
        if end > len(payload):
            raise ValueError("truncated class file")
        chunk = payload[offset:end]
        offset = end
        return chunk

    def read_uint(size: int) -> int:
        return int.from_bytes(read(size), "big")

    try:
        if read_uint(4) != 0xCAFEBABE:
            return False
        minor_version = read_uint(2)
        major_version = read_uint(2)
        if not (
            (major_version == 45 and minor_version <= 3)
            or (46 <= major_version <= 55)
            or (
                56 <= major_version <= PINNED_JAVA_CLASS_MAJOR_VERSION
                and minor_version == 0
            )
        ):
            return False
        constant_pool_count = read_uint(2)
        if constant_pool_count < 2:
            return False
        tags = [0] * constant_pool_count
        values: list[bytes | tuple[int, ...] | None] = [
            None
        ] * constant_pool_count
        index = 1
        while index < constant_pool_count:
            tag = read_uint(1)
            tags[index] = tag
            if tag == 1:
                value = read(read_uint(2))
                if not _valid_modified_utf8(value):
                    return False
                values[index] = value
            elif tag in {3, 4}:
                read(4)
            elif tag in {5, 6}:
                read(8)
                if index + 1 >= constant_pool_count:
                    return False
                index += 1
            elif tag in {7, 8, 16, 19, 20}:
                values[index] = (read_uint(2),)
            elif tag in {9, 10, 11, 12, 17, 18}:
                values[index] = (read_uint(2), read_uint(2))
            elif tag == 15:
                values[index] = (read_uint(1), read_uint(2))
            else:
                return False
            if (
                (tag in {15, 16, 18} and major_version < 51)
                or (tag in {19, 20} and major_version < 53)
                or (tag == 17 and major_version < 55)
            ):
                return False
            index += 1

        def has_tag(pool_index: int, *expected: int) -> bool:
            return (
                0 < pool_index < constant_pool_count
                and tags[pool_index] in expected
            )

        def class_name(
            pool_index: int,
            *,
            allow_array: bool,
        ) -> bytes | None:
            if not has_tag(pool_index, 7):
                return None
            class_value = values[pool_index]
            if not isinstance(class_value, tuple) or not has_tag(
                class_value[0],
                1,
            ):
                return None
            name = values[class_value[0]]
            if not isinstance(name, bytes) or not (
                _valid_internal_name(name)
                or (
                    allow_array
                    and name.startswith(b"[")
                    and _valid_field_descriptor(name)
                )
            ):
                return None
            return name

        dynamic_bootstrap_indices: list[int] = []
        invokeinterface_counts: dict[int, int] = {}
        for pool_index in range(1, constant_pool_count):
            tag = tags[pool_index]
            value = values[pool_index]
            if tag == 7:
                if class_name(pool_index, allow_array=True) is None:
                    return False
            elif tag == 8:
                if not isinstance(value, tuple) or not has_tag(value[0], 1):
                    return False
            elif tag in {19, 20}:
                return False
            elif tag == 16:
                if (
                    not isinstance(value, tuple)
                    or not has_tag(value[0], 1)
                    or not isinstance(values[value[0]], bytes)
                    or not _valid_method_descriptor(values[value[0]])
                ):
                    return False
            elif tag in {9, 10, 11}:
                if (
                    not isinstance(value, tuple)
                    or not has_tag(value[0], 7)
                    or not has_tag(value[1], 12)
                ):
                    return False
                name_and_type = values[value[1]]
                if not isinstance(name_and_type, tuple):
                    return False
                name = values[name_and_type[0]]
                descriptor = values[name_and_type[1]]
                if (
                    not isinstance(name, bytes)
                    or not _valid_unqualified_name(
                        name,
                        method=tag != 9,
                    )
                    or (
                        tag in {10, 11}
                        and name == b"<clinit>"
                    )
                    or (
                        tag == 11
                        and name == b"<init>"
                    )
                    or not isinstance(descriptor, bytes)
                    or not (
                        _valid_field_descriptor(descriptor)
                        if tag == 9
                        else _valid_method_descriptor(descriptor)
                    )
                    or (
                        name == b"<init>"
                        and not descriptor.endswith(b")V")
                    )
                ):
                    return False
                if tag == 11:
                    parameter_slots = _method_descriptor_parameter_slots(
                        descriptor
                    )
                    if parameter_slots is None:
                        return False
                    invokeinterface_counts[pool_index] = parameter_slots + 1
            elif tag == 12:
                if (
                    not isinstance(value, tuple)
                    or not has_tag(value[0], 1)
                    or not has_tag(value[1], 1)
                ):
                    return False
                name = values[value[0]]
                descriptor = values[value[1]]
                if not isinstance(name, bytes) or not isinstance(
                    descriptor,
                    bytes,
                ):
                    return False
                field_identity = (
                    _valid_unqualified_name(name, method=False)
                    and _valid_field_descriptor(descriptor)
                )
                method_identity = (
                    _valid_unqualified_name(name, method=True)
                    and _valid_method_descriptor(descriptor)
                    and (
                        name != b"<init>"
                        or descriptor.endswith(b")V")
                    )
                    and (
                        name != b"<clinit>"
                        or descriptor == b"()V"
                    )
                )
                if not field_identity and not method_identity:
                    return False
            elif tag == 15:
                reference_kind, reference_index = value
                if reference_kind in {1, 2, 3, 4}:
                    expected_reference_tags = (9,)
                elif reference_kind in {5, 8}:
                    expected_reference_tags = (10,)
                elif reference_kind in {6, 7}:
                    expected_reference_tags = (
                        (10, 11) if major_version >= 52 else (10,)
                    )
                else:
                    expected_reference_tags = (11,)
                if (
                    not isinstance(value, tuple)
                    or reference_kind not in range(1, 10)
                    or not has_tag(
                        reference_index,
                        *expected_reference_tags,
                    )
                ):
                    return False
                reference = values[reference_index]
                if not isinstance(reference, tuple):
                    return False
                name_and_type = values[reference[1]]
                if not isinstance(name_and_type, tuple):
                    return False
                method_name = values[name_and_type[0]]
                if (
                    reference_kind == 8
                    and method_name != b"<init>"
                ) or (
                    reference_kind in {5, 6, 7, 9}
                    and method_name in {b"<init>", b"<clinit>"}
                ):
                    return False
            elif tag in {17, 18}:
                if not isinstance(value, tuple) or not has_tag(value[1], 12):
                    return False
                dynamic_bootstrap_indices.append(value[0])
                name_and_type = values[value[1]]
                if not isinstance(name_and_type, tuple):
                    return False
                name = values[name_and_type[0]]
                descriptor = values[name_and_type[1]]
                if (
                    not isinstance(name, bytes)
                    or not _valid_unqualified_name(
                        name,
                        method=tag == 18,
                    )
                    or (
                        tag == 18
                        and name in {b"<init>", b"<clinit>"}
                    )
                    or not isinstance(descriptor, bytes)
                    or not (
                        _valid_field_descriptor(descriptor)
                        if tag == 17
                        else _valid_method_descriptor(descriptor)
                    )
                ):
                    return False

        class_access_flags = read_uint(2)
        if (
            class_access_flags & ~0x7631
            or (
                class_access_flags & 0x0010
                and class_access_flags & 0x0400
            )
            or (
                class_access_flags & 0x0200
                and (
                    not class_access_flags & 0x0400
                    or class_access_flags & (0x0010 | 0x0020 | 0x4000)
                )
            )
            or (
                class_access_flags & 0x2000
                and not class_access_flags & 0x0200
            )
        ):
            return False
        this_class = read_uint(2)
        super_class = read_uint(2)
        this_name = class_name(this_class, allow_array=False)
        super_name = (
            class_name(super_class, allow_array=False)
            if super_class != 0
            else None
        )
        if (
            this_name is None
            or (
                expected_name is not None
                and this_name != expected_name
            )
            or (
                super_class == 0
                and this_name != b"java/lang/Object"
            )
            or (
                super_class != 0
                and (
                    super_name is None
                    or super_name == this_name
                    or this_name == b"java/lang/Object"
                    or (
                        known_class_kinds is not None
                        and known_class_kinds.get(super_name) is True
                    )
                )
            )
            or (
                class_access_flags & 0x0200
                and super_name != b"java/lang/Object"
            )
        ):
            return False
        effective_class_kinds = dict(known_class_kinds or {})
        effective_class_kinds.setdefault(b"java/lang/Object", False)
        effective_class_kinds[this_name] = bool(
            class_access_flags & 0x0200
        )
        effective_superclasses = dict(known_superclasses or {})
        effective_superclasses[this_name] = super_name

        def valid_catch_class(catch_name: bytes) -> bool:
            if (
                catch_name == b"java/lang/Object"
                or effective_class_kinds.get(catch_name) is True
            ):
                return False
            current: bytes | None = catch_name
            visited: set[bytes] = set()
            while current is not None and current not in visited:
                if current == b"java/lang/Throwable":
                    return True
                if current == b"java/lang/Object":
                    return False
                visited.add(current)
                if current not in effective_superclasses:
                    return True
                current = effective_superclasses[current]
            return False

        interface_names: set[bytes] = set()
        for _ in range(read_uint(2)):
            interface_name = class_name(
                read_uint(2),
                allow_array=False,
            )
            if (
                interface_name is None
                or interface_name == this_name
                or interface_name in interface_names
                or interface_name == b"java/lang/Object"
                or (
                    known_class_kinds is not None
                    and known_class_kinds.get(interface_name) is False
                )
            ):
                return False
            interface_names.add(interface_name)

        bootstrap_method_count: int | None = None

        def read_attributes(
            *,
            allow_code: bool,
            class_level: bool = False,
            method_access_flags: int | None = None,
            method_name: bytes | None = None,
            method_descriptor: bytes | None = None,
            field_descriptor: bytes | None = None,
        ) -> bool:
            nonlocal bootstrap_method_count
            found_code = False
            singleton_attributes: set[bytes] = set()
            for _ in range(read_uint(2)):
                name_index = read_uint(2)
                if not has_tag(name_index, 1):
                    raise ValueError("invalid attribute name")
                attribute = read(read_uint(4))
                if values[name_index] == b"Code":
                    if not allow_code or found_code:
                        raise ValueError("invalid Code attribute placement")
                    code_offset = 0

                    def read_code(size: int) -> bytes:
                        nonlocal code_offset
                        end = code_offset + size
                        if end > len(attribute):
                            raise ValueError("truncated Code attribute")
                        chunk = attribute[code_offset:end]
                        code_offset = end
                        return chunk

                    def read_code_uint(size: int) -> int:
                        return int.from_bytes(read_code(size), "big")

                    max_stack = read_code_uint(2)
                    max_locals = read_code_uint(2)
                    code_length = read_code_uint(4)
                    if not 0 < code_length <= 65535:
                        raise ValueError("invalid bytecode length")
                    code = read_code(code_length)
                    instruction_offsets = _bytecode_instruction_offsets(
                        code,
                        constant_pool_tags=tags,
                        constant_pool_values=values,
                        invokeinterface_counts=invokeinterface_counts,
                        major_version=major_version,
                    )
                    if instruction_offsets is None:
                        raise ValueError("invalid bytecode instructions")
                    resource_requirements = _bytecode_resource_requirements(
                        code,
                        instruction_offsets=instruction_offsets,
                        constant_pool_values=values,
                    )
                    parameter_slots = (
                        _method_descriptor_parameter_slots(
                            method_descriptor,
                        )
                        if isinstance(method_descriptor, bytes)
                        else None
                    )
                    if (
                        resource_requirements is None
                        or parameter_slots is None
                        or method_access_flags is None
                        or method_name is None
                        or max_stack < resource_requirements[0]
                        or max_locals
                        < max(
                            resource_requirements[1],
                            parameter_slots
                            + (0 if method_access_flags & 0x0008 else 1),
                        )
                    ):
                        raise ValueError("invalid Code resource limits")
                    exception_table_length = read_code_uint(2)
                    exception_handlers: list[
                        tuple[int, int, int, str]
                    ] = []
                    if exception_table_length and max_stack < 1:
                        raise ValueError("invalid Code resource limits")
                    for _ in range(exception_table_length):
                        start_pc = read_code_uint(2)
                        end_pc = read_code_uint(2)
                        handler_pc = read_code_uint(2)
                        catch_type = read_code_uint(2)
                        catch_name = (
                            class_name(
                                catch_type,
                                allow_array=False,
                            )
                            if catch_type != 0
                            else b"java/lang/Throwable"
                        )
                        if (
                            start_pc >= end_pc
                            or end_pc > code_length
                            or handler_pc >= code_length
                            or start_pc not in instruction_offsets
                            or (
                                end_pc != code_length
                                and end_pc not in instruction_offsets
                            )
                            or handler_pc not in instruction_offsets
                            or catch_name is None
                            or not valid_catch_class(catch_name)
                        ):
                            raise ValueError("invalid exception table")
                        exception_handlers.append(
                            (
                                start_pc,
                                end_pc,
                                handler_pc,
                                "reference:"
                                f"L{catch_name.decode('latin-1')};",
                            )
                        )
                    required_stack_map_offsets: set[int] = set()
                    computed_states = _valid_operand_stack_flow(
                        code,
                        instruction_offsets=instruction_offsets,
                        constant_pool_tags=tags,
                        constant_pool_values=values,
                        exception_handlers=exception_handlers,
                        max_stack=max_stack,
                        max_locals=max_locals,
                        method_access_flags=method_access_flags,
                        method_name=method_name,
                        method_descriptor=method_descriptor,
                        this_name=this_name,
                        required_stack_map_offsets=(
                            required_stack_map_offsets
                            if major_version >= 51
                            else None
                        ),
                        major_version=major_version,
                        super_name=super_name,
                        known_class_kinds=effective_class_kinds,
                        known_superclasses=effective_superclasses,
                        direct_interfaces=interface_names,
                    )
                    if not isinstance(computed_states, dict):
                        raise ValueError("invalid operand stack flow")
                    found_stack_map_table = False
                    for _ in range(read_code_uint(2)):
                        nested_name = read_code_uint(2)
                        if not has_tag(nested_name, 1):
                            raise ValueError("invalid Code attribute name")
                        nested_attribute = read_code(read_code_uint(4))
                        nested_kind = values[nested_name]
                        if nested_kind == b"StackMapTable":
                            if found_stack_map_table or not (
                                _valid_stack_map_table(
                                    nested_attribute,
                                    code=code,
                                    constant_pool_tags=tags,
                                    constant_pool_values=values,
                                    instruction_offsets=instruction_offsets,
                                    computed_states=computed_states,
                                    required_frame_offsets=(
                                        required_stack_map_offsets
                                        if major_version >= 51
                                        else None
                                    ),
                                    known_class_kinds=(
                                        effective_class_kinds
                                    ),
                                    known_superclasses=(
                                        effective_superclasses
                                    ),
                                )
                            ):
                                raise ValueError("invalid StackMapTable")
                            found_stack_map_table = True
                        elif nested_kind == b"LineNumberTable":
                            if len(nested_attribute) < 2:
                                raise ValueError("invalid LineNumberTable")
                            entry_count = int.from_bytes(
                                nested_attribute[:2],
                                "big",
                            )
                            if len(nested_attribute) != 2 + 4 * entry_count:
                                raise ValueError("invalid LineNumberTable")
                            for entry_offset in range(
                                2,
                                len(nested_attribute),
                                4,
                            ):
                                start_pc = int.from_bytes(
                                    nested_attribute[
                                        entry_offset : entry_offset + 2
                                    ],
                                    "big",
                                )
                                if start_pc not in instruction_offsets:
                                    raise ValueError(
                                        "invalid LineNumberTable"
                                    )
                        elif nested_kind in {
                            b"LocalVariableTable",
                            b"LocalVariableTypeTable",
                        }:
                            if len(nested_attribute) < 2:
                                raise ValueError(
                                    "invalid local variable table"
                                )
                            entry_count = int.from_bytes(
                                nested_attribute[:2],
                                "big",
                            )
                            if len(nested_attribute) != 2 + 10 * entry_count:
                                raise ValueError(
                                    "invalid local variable table"
                                )
                            for entry_offset in range(
                                2,
                                len(nested_attribute),
                                10,
                            ):
                                start_pc = int.from_bytes(
                                    nested_attribute[
                                        entry_offset : entry_offset + 2
                                    ],
                                    "big",
                                )
                                variable_length = int.from_bytes(
                                    nested_attribute[
                                        entry_offset + 2 : entry_offset + 4
                                    ],
                                    "big",
                                )
                                variable_end = start_pc + variable_length
                                variable_name = int.from_bytes(
                                    nested_attribute[
                                        entry_offset + 4 : entry_offset + 6
                                    ],
                                    "big",
                                )
                                variable_type = int.from_bytes(
                                    nested_attribute[
                                        entry_offset + 6 : entry_offset + 8
                                    ],
                                    "big",
                                )
                                variable_index = int.from_bytes(
                                    nested_attribute[
                                        entry_offset + 8 : entry_offset + 10
                                    ],
                                    "big",
                                )
                                descriptor = (
                                    values[variable_type]
                                    if has_tag(variable_type, 1)
                                    else None
                                )
                                descriptor_slots = (
                                    _field_descriptor_end(descriptor)[1]
                                    if nested_kind
                                    == b"LocalVariableTable"
                                    and isinstance(descriptor, bytes)
                                    and _valid_field_descriptor(descriptor)
                                    else 1
                                )
                                if (
                                    start_pc not in instruction_offsets
                                    or variable_end > code_length
                                    or (
                                        variable_end != code_length
                                        and variable_end
                                        not in instruction_offsets
                                    )
                                    or not has_tag(variable_name, 1)
                                    or not has_tag(variable_type, 1)
                                    or variable_index + descriptor_slots
                                    > max_locals
                                ):
                                    raise ValueError(
                                        "invalid local variable table"
                                    )
                        elif nested_kind in {
                            b"Code",
                            b"BootstrapMethods",
                        }:
                            raise ValueError(
                                "invalid nested Code attribute placement"
                            )
                    if (
                        major_version >= 51
                        and required_stack_map_offsets
                        and not found_stack_map_table
                    ):
                        raise ValueError("missing StackMapTable")
                    if code_offset != len(attribute):
                        raise ValueError("trailing Code attribute data")
                    found_code = True
                elif values[name_index] == b"BootstrapMethods":
                    if not class_level or bootstrap_method_count is not None:
                        raise ValueError(
                            "invalid BootstrapMethods attribute placement"
                        )
                    bootstrap_offset = 0

                    def read_bootstrap_uint(size: int) -> int:
                        nonlocal bootstrap_offset
                        end = bootstrap_offset + size
                        if end > len(attribute):
                            raise ValueError(
                                "truncated BootstrapMethods attribute"
                            )
                        value = int.from_bytes(
                            attribute[bootstrap_offset:end],
                            "big",
                        )
                        bootstrap_offset = end
                        return value

                    bootstrap_method_count = read_bootstrap_uint(2)
                    for _ in range(bootstrap_method_count):
                        bootstrap_reference = read_bootstrap_uint(2)
                        bootstrap_handle = (
                            values[bootstrap_reference]
                            if has_tag(bootstrap_reference, 15)
                            else None
                        )
                        if (
                            not isinstance(bootstrap_handle, tuple)
                            or bootstrap_handle[0] not in {6, 8}
                        ):
                            raise ValueError("invalid bootstrap method")
                        for _ in range(read_bootstrap_uint(2)):
                            if not has_tag(
                                read_bootstrap_uint(2),
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                15,
                                16,
                                17,
                            ):
                                raise ValueError("invalid bootstrap argument")
                    if bootstrap_offset != len(attribute):
                        raise ValueError(
                            "trailing BootstrapMethods attribute data"
                        )
                elif values[name_index] == b"ConstantValue":
                    expected_tag = (
                        3
                        if field_descriptor in {
                            b"B",
                            b"C",
                            b"I",
                            b"S",
                            b"Z",
                        }
                        else 4
                        if field_descriptor == b"F"
                        else 5
                        if field_descriptor == b"J"
                        else 6
                        if field_descriptor == b"D"
                        else 8
                        if field_descriptor == b"Ljava/lang/String;"
                        else None
                    )
                    if (
                        field_descriptor is None
                        or b"ConstantValue" in singleton_attributes
                        or len(attribute) != 2
                        or expected_tag is None
                        or not has_tag(
                            int.from_bytes(attribute, "big"),
                            expected_tag,
                        )
                    ):
                        raise ValueError("invalid ConstantValue attribute")
                    singleton_attributes.add(b"ConstantValue")
                elif values[name_index] == b"Exceptions":
                    exception_count = (
                        int.from_bytes(attribute[:2], "big")
                        if len(attribute) >= 2
                        else -1
                    )
                    if (
                        method_descriptor is None
                        or b"Exceptions" in singleton_attributes
                        or len(attribute) < 2
                        or len(attribute) != 2 + 2 * exception_count
                        or any(
                            class_name(
                                int.from_bytes(
                                    attribute[
                                        entry_offset : entry_offset + 2
                                    ],
                                    "big",
                                ),
                                allow_array=False,
                            )
                            is None
                            for entry_offset in range(
                                2,
                                len(attribute),
                                2,
                            )
                        )
                    ):
                        raise ValueError("invalid Exceptions attribute")
                    singleton_attributes.add(b"Exceptions")
                elif values[name_index] == b"MethodParameters":
                    parameter_count = (
                        attribute[0] if attribute else -1
                    )
                    descriptor_parameter_count = (
                        _method_descriptor_parameter_count(
                            method_descriptor
                        )
                        if isinstance(method_descriptor, bytes)
                        else None
                    )
                    if (
                        method_descriptor is None
                        or b"MethodParameters" in singleton_attributes
                        or not attribute
                        or descriptor_parameter_count is None
                        or parameter_count != descriptor_parameter_count
                        or len(attribute) != 1 + 4 * parameter_count
                    ):
                        raise ValueError(
                            "invalid MethodParameters attribute"
                        )
                    for entry_offset in range(
                        1,
                        len(attribute),
                        4,
                    ):
                        parameter_name_index = int.from_bytes(
                            attribute[
                                entry_offset : entry_offset + 2
                            ],
                            "big",
                        )
                        parameter_access_flags = int.from_bytes(
                            attribute[
                                entry_offset + 2 : entry_offset + 4
                            ],
                            "big",
                        )
                        if (
                            parameter_name_index != 0
                            and not has_tag(parameter_name_index, 1)
                        ) or parameter_access_flags & ~0x9010:
                            raise ValueError(
                                "invalid MethodParameters attribute"
                            )
                    singleton_attributes.add(b"MethodParameters")
                elif values[name_index] == b"InnerClasses":
                    inner_class_count = (
                        int.from_bytes(attribute[:2], "big")
                        if len(attribute) >= 2
                        else -1
                    )
                    if (
                        not class_level
                        or b"InnerClasses" in singleton_attributes
                        or len(attribute) < 2
                        or len(attribute) != 2 + 8 * inner_class_count
                    ):
                        raise ValueError("invalid InnerClasses attribute")
                    for entry_offset in range(
                        2,
                        len(attribute),
                        8,
                    ):
                        inner_class_index = int.from_bytes(
                            attribute[entry_offset : entry_offset + 2],
                            "big",
                        )
                        outer_class_index = int.from_bytes(
                            attribute[
                                entry_offset + 2 : entry_offset + 4
                            ],
                            "big",
                        )
                        inner_name_index = int.from_bytes(
                            attribute[
                                entry_offset + 4 : entry_offset + 6
                            ],
                            "big",
                        )
                        inner_access_flags = int.from_bytes(
                            attribute[
                                entry_offset + 6 : entry_offset + 8
                            ],
                            "big",
                        )
                        inner_name = (
                            values[inner_name_index]
                            if has_tag(inner_name_index, 1)
                            else None
                        )
                        if (
                            class_name(
                                inner_class_index,
                                allow_array=False,
                            )
                            is None
                            or (
                                outer_class_index != 0
                                and class_name(
                                    outer_class_index,
                                    allow_array=False,
                                )
                                is None
                            )
                            or (
                                inner_name_index != 0
                                and (
                                    not isinstance(inner_name, bytes)
                                    or not _valid_unqualified_name(
                                        inner_name,
                                        method=False,
                                    )
                                )
                            )
                            or inner_access_flags & ~0x761F
                        ):
                            raise ValueError(
                                "invalid InnerClasses attribute"
                            )
                    singleton_attributes.add(b"InnerClasses")
                elif values[name_index] == b"Record":
                    if (
                        not class_level
                        or major_version < 60
                        or b"Record" in singleton_attributes
                        or len(attribute) < 2
                    ):
                        raise ValueError("invalid Record attribute")
                    record_offset = 0

                    def read_record_uint(size: int) -> int:
                        nonlocal record_offset
                        end = record_offset + size
                        if end > len(attribute):
                            raise ValueError("truncated Record attribute")
                        value = int.from_bytes(
                            attribute[record_offset:end],
                            "big",
                        )
                        record_offset = end
                        return value

                    component_signatures: set[
                        tuple[bytes, bytes]
                    ] = set()
                    for _ in range(read_record_uint(2)):
                        component_name_index = read_record_uint(2)
                        component_descriptor_index = read_record_uint(2)
                        component_name = (
                            values[component_name_index]
                            if has_tag(component_name_index, 1)
                            else None
                        )
                        component_descriptor = (
                            values[component_descriptor_index]
                            if has_tag(component_descriptor_index, 1)
                            else None
                        )
                        component_signature = (
                            (component_name, component_descriptor)
                            if isinstance(component_name, bytes)
                            and isinstance(component_descriptor, bytes)
                            else None
                        )
                        if (
                            component_signature is None
                            or component_signature
                            in component_signatures
                            or not _valid_unqualified_name(
                                component_name,
                                method=False,
                            )
                            or not _valid_field_descriptor(
                                component_descriptor
                            )
                        ):
                            raise ValueError(
                                "invalid Record component"
                            )
                        component_signatures.add(component_signature)
                        component_attributes: set[bytes] = set()
                        for _ in range(read_record_uint(2)):
                            component_attribute_name_index = (
                                read_record_uint(2)
                            )
                            if not has_tag(
                                component_attribute_name_index,
                                1,
                            ):
                                raise ValueError(
                                    "invalid Record component attribute"
                                )
                            component_attribute_length = (
                                read_record_uint(4)
                            )
                            component_attribute_end = (
                                record_offset
                                + component_attribute_length
                            )
                            if component_attribute_end > len(attribute):
                                raise ValueError(
                                    "truncated Record component attribute"
                                )
                            component_attribute = attribute[
                                record_offset:component_attribute_end
                            ]
                            record_offset = component_attribute_end
                            component_attribute_name = values[
                                component_attribute_name_index
                            ]
                            if component_attribute_name == b"Signature":
                                if (
                                    b"Signature"
                                    in component_attributes
                                    or len(component_attribute) != 2
                                    or not has_tag(
                                        int.from_bytes(
                                            component_attribute,
                                            "big",
                                        ),
                                        1,
                                    )
                                ):
                                    raise ValueError(
                                        "invalid Record component Signature"
                                    )
                                component_attributes.add(b"Signature")
                            elif component_attribute_name in {
                                b"Code",
                                b"ConstantValue",
                                b"Exceptions",
                                b"InnerClasses",
                                b"Record",
                                b"BootstrapMethods",
                                b"SourceFile",
                            }:
                                raise ValueError(
                                    "invalid Record component attribute"
                                )
                    if record_offset != len(attribute):
                        raise ValueError("trailing Record attribute data")
                    singleton_attributes.add(b"Record")
                elif values[name_index] == b"EnclosingMethod":
                    enclosing_class_index = (
                        int.from_bytes(attribute[:2], "big")
                        if len(attribute) == 4
                        else 0
                    )
                    enclosing_method_index = (
                        int.from_bytes(attribute[2:], "big")
                        if len(attribute) == 4
                        else -1
                    )
                    enclosing_method = (
                        values[enclosing_method_index]
                        if has_tag(enclosing_method_index, 12)
                        else None
                    )
                    enclosing_method_name = (
                        values[enclosing_method[0]]
                        if isinstance(enclosing_method, tuple)
                        and has_tag(enclosing_method[0], 1)
                        else None
                    )
                    enclosing_method_descriptor = (
                        values[enclosing_method[1]]
                        if isinstance(enclosing_method, tuple)
                        and has_tag(enclosing_method[1], 1)
                        else None
                    )
                    if (
                        not class_level
                        or b"EnclosingMethod" in singleton_attributes
                        or len(attribute) != 4
                        or class_name(
                            enclosing_class_index,
                            allow_array=False,
                        )
                        is None
                        or (
                            enclosing_method_index != 0
                            and (
                                not isinstance(
                                    enclosing_method_name,
                                    bytes,
                                )
                                or not isinstance(
                                    enclosing_method_descriptor,
                                    bytes,
                                )
                                or not _valid_unqualified_name(
                                    enclosing_method_name,
                                    method=True,
                                )
                                or not _valid_method_descriptor(
                                    enclosing_method_descriptor
                                )
                            )
                        )
                    ):
                        raise ValueError("invalid EnclosingMethod attribute")
                    singleton_attributes.add(b"EnclosingMethod")
                elif values[name_index] == b"NestHost":
                    nest_host_index = (
                        int.from_bytes(attribute, "big")
                        if len(attribute) == 2
                        else 0
                    )
                    nest_host_name = class_name(
                        nest_host_index,
                        allow_array=False,
                    )
                    if (
                        not class_level
                        or major_version < 55
                        or b"NestHost" in singleton_attributes
                        or len(attribute) != 2
                        or nest_host_name is None
                        or nest_host_name == this_name
                    ):
                        raise ValueError("invalid NestHost attribute")
                    singleton_attributes.add(b"NestHost")
                elif values[name_index] == b"Signature":
                    if (
                        b"Signature" in singleton_attributes
                        or len(attribute) != 2
                        or not has_tag(
                            int.from_bytes(attribute, "big"),
                            1,
                        )
                    ):
                        raise ValueError("invalid Signature attribute")
                    singleton_attributes.add(b"Signature")
                elif values[name_index] == b"SourceFile":
                    if (
                        not class_level
                        or b"SourceFile" in singleton_attributes
                        or len(attribute) != 2
                        or not has_tag(
                            int.from_bytes(attribute, "big"),
                            1,
                        )
                    ):
                        raise ValueError("invalid SourceFile attribute")
                    singleton_attributes.add(b"SourceFile")
                elif values[name_index] in {b"Synthetic", b"Deprecated"}:
                    attribute_kind = values[name_index]
                    if (
                        attribute_kind in singleton_attributes
                        or attribute
                    ):
                        raise ValueError("invalid marker attribute")
                    singleton_attributes.add(attribute_kind)
            return found_code

        def read_members(*, methods: bool) -> None:
            signatures: set[tuple[bytes, bytes]] = set()
            for _ in range(read_uint(2)):
                access_flags = read_uint(2)
                name_index = read_uint(2)
                descriptor_index = read_uint(2)
                name = values[name_index] if has_tag(name_index, 1) else None
                descriptor = values[descriptor_index] if has_tag(
                    descriptor_index,
                    1,
                ) else None
                signature = (
                    (name, descriptor)
                    if isinstance(name, bytes)
                    and isinstance(descriptor, bytes)
                    else None
                )
                invalid_access_flags = (
                    sum(
                        bool(access_flags & visibility)
                        for visibility in (0x0001, 0x0002, 0x0004)
                    )
                    > 1
                )
                if methods:
                    invalid_access_flags = (
                        invalid_access_flags
                        or bool(access_flags & ~0x1DFF)
                        or bool(
                            access_flags & 0x0400
                            and access_flags & 0x093A
                        )
                    )
                    if (
                        class_access_flags & 0x0200
                        and name != b"<clinit>"
                    ):
                        invalid_access_flags = (
                            invalid_access_flags
                            or bool(
                                access_flags
                                & (0x0004 | 0x0010 | 0x0020 | 0x0100)
                            )
                            or (
                                major_version < 52
                                and not access_flags & 0x0400
                            )
                            or (
                                major_version < 53
                                and not access_flags & 0x0001
                            )
                            or (
                                major_version >= 53
                                and bool(access_flags & 0x0001)
                                == bool(access_flags & 0x0002)
                            )
                        )
                else:
                    invalid_access_flags = (
                        invalid_access_flags
                        or bool(access_flags & ~0x50DF)
                        or bool(
                            access_flags & 0x0010
                            and access_flags & 0x0040
                        )
                        or bool(
                            class_access_flags & 0x0200
                            and (
                                access_flags & 0x0019 != 0x0019
                                or access_flags & ~(0x0019 | 0x1000)
                            )
                        )
                    )
                if (
                    signature is None
                    or signature in signatures
                    or invalid_access_flags
                    or not _valid_unqualified_name(
                        name,
                        method=methods,
                    )
                    or not isinstance(descriptor, bytes)
                    or not (
                        _valid_method_descriptor(
                            descriptor,
                            max_parameter_slots=(
                                255 if access_flags & 0x0008 else 254
                            ),
                        )
                        if methods
                        else _valid_field_descriptor(descriptor)
                    )
                    or (
                        methods
                        and name == b"<init>"
                        and (
                            class_access_flags & 0x0200
                            or access_flags & 0x0578
                            or not descriptor.endswith(b")V")
                        )
                    )
                    or (
                        methods
                        and name == b"<clinit>"
                        and (
                            descriptor != b"()V"
                            or (
                                major_version >= 51
                                and not access_flags & 0x0008
                            )
                        )
                    )
                ):
                    raise ValueError("invalid member identity")
                signatures.add(signature)
                found_code = read_attributes(
                    allow_code=methods,
                    method_access_flags=access_flags if methods else None,
                    method_name=name if methods else None,
                    method_descriptor=descriptor if methods else None,
                    field_descriptor=descriptor if not methods else None,
                )
                if methods and (
                    found_code == bool(access_flags & (0x0100 | 0x0400))
                ):
                    raise ValueError("invalid method Code attribute")

        read_members(methods=False)
        read_members(methods=True)
        read_attributes(allow_code=False, class_level=True)
        if dynamic_bootstrap_indices and (
            bootstrap_method_count is None
            or any(
                index >= bootstrap_method_count
                for index in dynamic_bootstrap_indices
            )
        ):
            return False
        return offset == len(payload)
    except ValueError:
        return False


def _multi_release_jar_enabled(archive: zipfile.ZipFile) -> bool:
    try:
        payload = archive.read("META-INF/MANIFEST.MF")
    except KeyError:
        return False
    physical_lines = payload.splitlines()
    logical_lines: list[bytes] = []
    for line in physical_lines:
        if not line:
            break
        if line.startswith(b" "):
            if not logical_lines:
                return False
            logical_lines[-1] += line[1:]
        else:
            logical_lines.append(line)
    attributes: dict[bytes, bytes] = {}
    for line in logical_lines:
        if b": " not in line:
            return False
        name, value = line.split(b": ", 1)
        try:
            normalized_name = name.decode("ascii").casefold().encode("ascii")
        except UnicodeDecodeError:
            return False
        if normalized_name in attributes:
            return False
        attributes[normalized_name] = value
    return (
        attributes.get(b"manifest-version") == b"1.0"
        and attributes.get(b"multi-release", b"").lower() == b"true"
    )


def _class_identity_and_kind(
    payload: bytes,
) -> tuple[bytes, bool, bytes | None, bool] | None:
    offset = 0

    def read(size: int) -> bytes:
        nonlocal offset
        end = offset + size
        if end > len(payload):
            raise ValueError("truncated class header")
        value = payload[offset:end]
        offset = end
        return value

    def read_uint(size: int) -> int:
        return int.from_bytes(read(size), "big")

    try:
        if read_uint(4) != 0xCAFEBABE:
            return None
        read(4)
        constant_pool_count = read_uint(2)
        values: list[object | None] = [None] * constant_pool_count
        index = 1
        while index < constant_pool_count:
            tag = read_uint(1)
            if tag == 1:
                values[index] = read(read_uint(2))
            elif tag in {3, 4}:
                read(4)
            elif tag in {5, 6}:
                read(8)
                index += 1
            elif tag in {7, 8, 16, 19, 20}:
                values[index] = (read_uint(2),)
            elif tag in {9, 10, 11, 12, 17, 18}:
                read(4)
            elif tag == 15:
                read(3)
            else:
                return None
            index += 1
        access_flags = read_uint(2)
        this_class = read_uint(2)
        super_class = read_uint(2)
        if not 0 < this_class < len(values):
            return None
        class_value = values[this_class]
        if not isinstance(class_value, tuple) or len(class_value) != 1:
            return None
        name_index = class_value[0]
        if (
            not isinstance(name_index, int)
            or not 0 < name_index < len(values)
        ):
            return None
        name = values[name_index]
        if not isinstance(name, bytes):
            return None
        if super_class == 0:
            super_name = None
        else:
            if not 0 < super_class < len(values):
                return None
            super_value = values[super_class]
            if not isinstance(super_value, tuple) or len(super_value) != 1:
                return None
            super_name_index = super_value[0]
            if (
                not isinstance(super_name_index, int)
                or not 0 < super_name_index < len(values)
            ):
                return None
            super_name = values[super_name_index]
            if not isinstance(super_name, bytes):
                return None
        return (
            name,
            bool(access_flags & 0x0200),
            super_name,
            bool(access_flags & 0x0010),
        )
    except ValueError:
        return None


def _jar_local_hierarchy_acyclic(
    superclasses: Mapping[bytes, bytes | None],
) -> bool:
    completed: set[bytes] = set()
    for start in superclasses:
        active: set[bytes] = set()
        path: list[bytes] = []
        current: bytes | None = start
        while (
            current is not None
            and current in superclasses
            and current not in completed
        ):
            if current in active:
                return False
            active.add(current)
            path.append(current)
            current = superclasses[current]
        completed.update(path)
    return True


def _jar_contains_bytecode(archive: zipfile.ZipFile, path: str) -> bool:
    try:
        multi_release_enabled: bool | None = None
        active_classes: dict[str, tuple[int, zipfile.ZipInfo]] = {}
        for entry in archive.infolist():
            if not entry.filename.endswith(".class"):
                continue
            class_path = entry.filename[:-6]
            version = 0
            if class_path.startswith("META-INF/versions/"):
                parts = class_path.split("/", 3)
                if len(parts) != 4 or not parts[2].isdigit():
                    continue
                version = int(parts[2])
                if (
                    str(version) != parts[2]
                    or not 9
                    <= version
                    <= PINNED_JAVA_CLASS_MAJOR_VERSION - 44
                ):
                    continue
                if multi_release_enabled is None:
                    multi_release_enabled = _multi_release_jar_enabled(
                        archive
                    )
                if not multi_release_enabled:
                    continue
                class_path = parts[3]
            selected = active_classes.get(class_path)
            if selected is None or version > selected[0]:
                active_classes[class_path] = (version, entry)
        known_class_kinds: dict[bytes, bool] = {
            b"java/lang/Object": False,
        }
        known_superclasses: dict[bytes, bytes | None] = {}
        known_final_classes: set[bytes] = set()
        class_payloads: list[tuple[bytes, str]] = []
        for class_path, (_, entry) in active_classes.items():
            class_payload = archive.read(entry)
            class_payloads.append((class_payload, class_path))
            identity = _class_identity_and_kind(class_payload)
            if identity is None:
                continue
            name, is_interface, declared_super_name, is_final = identity
            previous_kind = known_class_kinds.get(name)
            if previous_kind is not None and previous_kind != is_interface:
                return False
            known_class_kinds[name] = is_interface
            known_superclasses[name] = declared_super_name
            if is_final:
                known_final_classes.add(name)
        if (
            any(
                super_name in known_final_classes
                for super_name in known_superclasses.values()
                if super_name is not None
            )
            or not _jar_local_hierarchy_acyclic(known_superclasses)
        ):
            return False
        for class_payload, class_path in class_payloads:
            if _valid_class_file(
                class_payload,
                expected_name=_encode_modified_utf8(class_path),
                known_class_kinds=known_class_kinds,
                known_superclasses=known_superclasses,
            ):
                return True
        return False
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        _fail(
            "MAVEN_SBOM_ROOTFS",
            f"{path} contains unreadable bytecode: {error}",
        )


def _maven_properties(payload: bytes, path: str) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        _fail("MAVEN_SBOM_ROOTFS", f"{path} has non-UTF-8 pom.properties: {error}")

    def unescape(value: str) -> str:
        result: list[str] = []
        offset = 0
        while offset < len(value):
            if value[offset] != "\\":
                result.append(value[offset])
                offset += 1
                continue
            offset += 1
            if offset >= len(value):
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} has malformed pom.properties escape",
                )
            escaped = value[offset]
            if escaped == "u":
                digits = value[offset + 1 : offset + 5]
                if len(digits) != 4 or any(
                    character not in "0123456789abcdefABCDEF"
                    for character in digits
                ):
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} has malformed pom.properties escape",
                    )
                result.append(chr(int(digits, 16)))
                offset += 5
                continue
            result.append(
                {
                    "t": "\t",
                    "n": "\n",
                    "r": "\r",
                    "f": "\f",
                }.get(escaped, escaped)
            )
            offset += 1
        return "".join(result)

    logical_lines: list[str] = []
    pending = ""
    for physical_line in text.splitlines():
        line = (
            pending + physical_line.lstrip(" \t\f")
            if pending
            else physical_line
        )
        trailing_backslashes = len(line) - len(line.rstrip("\\"))
        if trailing_backslashes % 2:
            pending = line[:-1]
            continue
        logical_lines.append(line)
        pending = ""
    if pending:
        logical_lines.append(pending)

    properties: dict[str, str] = {}
    for line in logical_lines:
        content = line.lstrip(" \t\f")
        if not content or content.startswith(("#", "!")):
            continue
        escaped = False
        separator_offset = len(content)
        whitespace_separator = False
        for offset, character in enumerate(content):
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character in "=:" or character in " \t\f":
                separator_offset = offset
                whitespace_separator = character in " \t\f"
                break
        value_offset = separator_offset
        if separator_offset < len(content):
            if whitespace_separator:
                while (
                    value_offset < len(content)
                    and content[value_offset] in " \t\f"
                ):
                    value_offset += 1
                if (
                    value_offset < len(content)
                    and content[value_offset] in "=:"
                ):
                    value_offset += 1
            else:
                value_offset += 1
            while (
                value_offset < len(content)
                and content[value_offset] in " \t\f"
            ):
                value_offset += 1
        key = unescape(content[:separator_offset])
        value = unescape(content[value_offset:])
        if not key or key in properties:
            _fail("MAVEN_SBOM_ROOTFS", f"{path} repeats {key!r}")
        properties[key] = value
    return properties


def _valid_java_binary_name(value: str) -> bool:
    def valid_start(character: str) -> bool:
        category = unicodedata.category(character)
        return (
            character in {"$", "_"}
            or category.startswith("L")
            or category in {"Nl", "Sc", "Pc"}
        )

    def valid_part(character: str) -> bool:
        return valid_start(character) or unicodedata.category(
            character
        ) in {"Mn", "Mc", "Nd", "Cf"}

    return all(
        component
        and valid_start(component[0])
        and all(valid_part(character) for character in component[1:])
        for component in value.split(".")
    )


def _valid_service_provider_configuration(
    archive: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
) -> bool:
    prefix = "META-INF/services/"
    if not entry.filename.startswith(prefix):
        return False
    service_name = entry.filename.removeprefix(prefix)
    if (
        not service_name
        or "/" in service_name
        or not _valid_java_binary_name(service_name)
    ):
        return False
    try:
        text = archive.read(entry).decode("utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError, zipfile.BadZipFile):
        return False
    return all(
        not provider
        or _valid_java_binary_name(provider)
        for provider in (
            line.split("#", 1)[0].strip()
            for line in text.splitlines()
        )
    )


def _validate_rootfs_discovery_omissions(
    repository_path: Path,
    records: Mapping[str, Mapping[str, Any]],
    missing_paths: set[str],
    rootfs_components: list[dict[str, Any]],
) -> dict[str, str]:
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
    permitted_classifier_metadata = re.compile(
        r"META-INF/(?:"
        r"MANIFEST\.MF|"
        r"(?:LICENSE|NOTICE)(?:\.[^/]*)?|"
        r"DEPENDENCIES|"
        r"maven/[^/]+/[^/]+/pom\.(?:xml|properties)"
        r")\Z"
    )
    for path in sorted(
        missing_paths,
        key=lambda item: (bool(_maven_classifier(item)), item),
    ):
        record = records[path]
        candidate = repository_root / path
        try:
            resolved_candidate = candidate.resolve(strict=True)
            candidate_stat = candidate.lstat()
            candidate_mode = stat.S_IMODE(candidate_stat.st_mode)
        except OSError as error:
            _fail("MAVEN_SBOM_ROOTFS", f"{candidate}: {error}")
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
            candidate,
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
        classifier = _maven_classifier(path)
        purl = _maven_purl(path)
        base_purl = purl.split("?classifier=", 1)[0]
        artifact, version = path.split("/")[-3:-1]
        base_path = (
            path.rsplit("/", 1)[0]
            + f"/{artifact}-{version}.jar"
        )
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
                    "rootfs or manifest-verified base coordinate"
                ),
            )
        archive, names = _jar_entries(payload, path)
        try:
            if classifier == "sources":
                source_entries = [
                    entry
                    for entry in archive.infolist()
                    if entry.filename.endswith(
                        (".java", ".kt", ".kts", ".scala", ".groovy")
                    )
                ]
                unexpected_entries = [
                    entry.filename
                    for entry in archive.infolist()
                    if not entry.is_dir()
                    and entry not in source_entries
                    and permitted_classifier_metadata.fullmatch(
                        entry.filename
                    )
                    is None
                ]
                if (
                    any(name.endswith(".class") for name in names)
                    or not source_entries
                    or unexpected_entries
                ):
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} is not a source-only classifier JAR",
                    )
                try:
                    contains_nested_zip = _archive_contains_nested_zip(
                        archive
                    )
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} contains unreadable source: {error}",
                    )
                if contains_nested_zip:
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} contains an undiscovered nested JAR",
                    )
                discovery[path] = "manifest-supplemental-sources"
                continue
            if classifier == "tests":
                permitted_test_suffixes = (
                    ".class",
                    ".java",
                    ".kt",
                    ".kts",
                    ".scala",
                    ".groovy",
                    ".properties",
                    ".xml",
                    ".json",
                    ".yaml",
                    ".yml",
                    ".txt",
                    ".csv",
                    ".sql",
                    ".conf",
                    ".config",
                    ".html",
                    ".js",
                    ".css",
                    ".proto",
                    ".avsc",
                )
                unexpected_entries = [
                    entry.filename
                    for entry in archive.infolist()
                    if not entry.is_dir()
                    and not entry.filename.endswith(permitted_test_suffixes)
                    and permitted_classifier_metadata.fullmatch(
                        entry.filename
                    )
                    is None
                    and not _valid_service_provider_configuration(
                        archive,
                        entry,
                    )
                ]
                if not _jar_contains_bytecode(archive, path):
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} contains no test bytecode",
                    )
                if unexpected_entries:
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} is not a test-only classifier JAR",
                    )
                try:
                    contains_nested_zip = _archive_contains_nested_zip(
                        archive
                    )
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} contains unreadable test payload: {error}",
                    )
                if contains_nested_zip:
                    _fail(
                        "MAVEN_SBOM_ROOTFS",
                        f"{path} contains an undiscovered nested JAR",
                    )
                discovery[path] = "manifest-supplemental-tests"
                continue
            if classifier:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} uses an unreviewed omitted classifier",
                )
            group = ".".join(path.split("/")[:-3])
            artifact, version = path.split("/")[-3:-1]
            properties_path = (
                "META-INF/maven/"
                f"{group}/{artifact}/pom.properties"
            )
            coordinate_properties_paths = [
                name
                for name in names
                if name.startswith("META-INF/maven/")
                and name.endswith("/pom.properties")
            ]
            if coordinate_properties_paths != [properties_path]:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    (
                        f"{path} does not contain exactly its Maven "
                        "pom.properties"
                    ),
                )
            properties = _maven_properties(
                archive.read(properties_path),
                path,
            )
            if properties != {
                "artifactId": artifact,
                "groupId": group,
                "version": version,
            }:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} pom.properties coordinates differ",
                )
            if not _jar_contains_bytecode(archive, path):
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    (
                        f"{path} contains no bytecode for "
                        "coordinate verification"
                    ),
                )
            try:
                contains_nested_zip = _archive_contains_nested_zip(
                    archive
                )
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} contains unreadable archive member: {error}",
                )
            if contains_nested_zip:
                _fail(
                    "MAVEN_SBOM_ROOTFS",
                    f"{path} contains an undiscovered nested JAR",
                )
            if (base_purl, path) in rootfs_identities:
                discovery[path] = "manifest-rootfs-purl-deduplicated"
            else:
                manifest_verified_base_purls.add(base_purl)
                discovery[path] = "manifest-coordinate-verified"
        finally:
            archive.close()
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
    discovery_omissions = _validate_rootfs_discovery_omissions(
        repository_path,
        records,
        expected_paths - observed_rootfs,
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
                    "name": "shirokuma:rootfs-audited-omissions",
                    "value": str(len(discovery_omissions)),
                },
                {
                    "name": "shirokuma:rootfs-audited-supplemental-jars",
                    "value": str(
                        sum(
                            mode.startswith("manifest-supplemental-")
                            for mode in discovery_omissions.values()
                        )
                    ),
                },
                {
                    "name": "shirokuma:rootfs-purl-deduplicated-jars",
                    "value": str(
                        sum(
                            mode == "manifest-rootfs-purl-deduplicated"
                            for mode in discovery_omissions.values()
                        )
                    ),
                },
                {
                    "name": "shirokuma:manifest-coordinate-verified-jars",
                    "value": str(
                        sum(
                            mode == "manifest-coordinate-verified"
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


def _trivy_package_identities(report_path: Path) -> set[tuple[str, str]]:
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
        if any(
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
    observed_identities = _trivy_package_identities(report_path)
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
    if document.get("SchemaVersion") != 2:
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
        TEST_PATH,
        BUN_TEST_PATH,
        PARQUET_REMEDIATION_TEST_PATH,
        SOURCE_OVERLAY_PATH,
        DISTRIBUTION_REMEDIATION_PATH,
        VEX_PATH,
        OVERLAY_ADR_PATH,
        PARQUET_REMEDIATION_ADR_PATH,
        DISTRIBUTION_REMEDIATION_ADR_PATH,
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
        "publication-status --root .",
        (
            "Trino dependency publication is blocked pending "
            "owner-authorized source remediation"
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
        or lines.count("          include-hidden-files: true") != 2
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
        publication.get("permitted") is not True
        or publication.get("workflow_present") is not True
        or publication.get("workflow") != WORKFLOW_PATH.as_posix()
        or publication.get("allowed_ref") != "refs/heads/main"
        or publication.get("artifact_role") != "review_pending_dependency_evidence"
        or publication.get("retire_in_evidence_review_pr") is not True
        or publication.get("pull_request_behavior")
        != (
            "static_and_authorized_source_overlay_and_remediation_"
            "validation_without_publication"
        )
        or publication.get("evidence_review_inventory_policy")
        != {
            "recursive_closed_world_required": True,
            "regular_files_only": True,
            "directories_and_symlinks_rejected": True,
        }
    ):
        _fail("PUBLICATION", "blocked publication lifecycle differs")
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


def _validate_policy_hashes(root: Path, contract: Mapping[str, Any]) -> None:
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
        VEX_PATH,
        OVERLAY_ADR_PATH,
        PARQUET_REMEDIATION_ADR_PATH,
        DISTRIBUTION_REMEDIATION_ADR_PATH,
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
    lifecycle = contract.get("lifecycle", {})
    if lifecycle != {
        "state": "dependency_snapshot_publication_pending",
        "contract_only": False,
        "dependency_artifact_present": False,
        "publication_workflow_permitted": True,
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
        or dependency_resolution.get("external_inputs")
        != [
            EXPECTED_BUN_INPUT,
            EXPECTED_PARQUET_SOURCE_REMEDIATION,
        ]
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
    provenance = snapshot.get("authentication", {}).get("provenance", {})
    if (
        provenance.get("parquet_source_remediation_resolved_dependency")
        != EXPECTED_PARQUET_SLSA_RESOLVED_DEPENDENCY
    ):
        _fail(
            "SLSA_SOURCE_REMEDIATION",
            "Parquet source remediation provenance binding differs",
        )
    repository_state = admission.get("repository_state", {})
    if (
        admission.get("source_overlay_authorization")
        != EXPECTED_ADMISSION_OVERLAY_AUTHORIZATION
        or admission.get("source_remediation_authorization")
        != EXPECTED_ADMISSION_SOURCE_REMEDIATION_AUTHORIZATION
        or admission.get("distribution_remediation_authorization")
        != EXPECTED_ADMISSION_DISTRIBUTION_REMEDIATION_AUTHORIZATION
        or repository_state.get("publication_workflow_permitted") is not True
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
    permissions = {
        contract.get("lifecycle", {}).get("publication_workflow_permitted"),
        contract.get("publication", {}).get("permitted"),
        admission.get("repository_state", {}).get(
            "publication_workflow_permitted"
        ),
    }
    if permissions == {True}:
        return "active"
    if permissions == {False}:
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
