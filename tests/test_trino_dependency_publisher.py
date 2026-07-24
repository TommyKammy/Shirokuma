from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_trino_maven_dependencies as package  # noqa: E402
import verify_trino_dependency_publisher as verify  # noqa: E402


class MavenSnapshotTests(unittest.TestCase):
    def _repository(self, root: Path) -> Path:
        repository = root / "repository"
        artifact = repository / "org/example/demo/1.0"
        artifact.mkdir(parents=True)
        (artifact / "demo-1.0.jar").write_bytes(b"jar")
        (artifact / "demo-1.0.pom").write_text("<project/>\n", encoding="utf-8")
        (artifact / "_remote.repositories").write_text(
            "# generated\n"
            "demo-1.0.jar>central=\n"
            "demo-1.0.pom>shirokuma-central-fallback=\n",
            encoding="iso-8859-1",
        )
        metadata = repository / "io/confluent/sample"
        metadata.mkdir(parents=True)
        (metadata / "maven-metadata-confluent.xml").write_text(
            "<metadata/>\n", encoding="utf-8"
        )
        return repository

    def test_create_is_deterministic_and_verify_reconstructs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_repository = self._repository(root / "first")
            second_repository = self._repository(root / "second")
            first_descriptor = root / "first.json"
            second_descriptor = root / "second.json"
            first_archive = root / "first.tar.gz"
            second_archive = root / "second.tar.gz"
            package.create_snapshot(
                first_repository, first_descriptor, first_archive
            )
            package.create_snapshot(
                second_repository, second_descriptor, second_archive
            )
            self.assertEqual(
                first_descriptor.read_bytes(), second_descriptor.read_bytes()
            )
            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            extracted = root / "extracted"
            package.verify_snapshot(first_descriptor, first_archive, extracted)
            self.assertEqual(b"jar", (extracted / "org/example/demo/1.0/demo-1.0.jar").read_bytes())

    def test_manifest_records_only_closed_origins_and_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            self.assertEqual(manifest["file_count"], len(manifest["files"]))
            self.assertEqual(
                manifest["total_bytes"],
                sum(record["size"] for record in manifest["files"]),
            )
            self.assertEqual(
                sorted(
                    record["path"].encode("utf-8")
                    for record in manifest["files"]
                ),
                [record["path"].encode("utf-8") for record in manifest["files"]],
            )
            self.assertEqual(
                {"path", "size", "mode", "sha256", "repository_origin"},
                set(manifest["files"][0]),
            )
            self.assertEqual(
                set(package.ALLOWED_REPOSITORIES.values()),
                {record["repository_origin"] for record in manifest["files"]},
            )
            self.assertEqual(
                sorted(package.EXCLUDED_RESOLVER_METADATA),
                manifest["excluded_resolver_metadata"],
            )
            self.assertFalse(
                {
                    Path(record["path"]).name
                    for record in manifest["files"]
                }
                & package.EXCLUDED_RESOLVER_METADATA
            )
            with gzip.GzipFile(fileobj=io.BytesIO(archive.read_bytes())) as stream:
                stream.read(1)
                self.assertEqual(0, stream.mtime)
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    self.assertTrue(member.isfile())
                    self.assertEqual((0, 0, 0, 0o644), (
                        member.uid,
                        member.gid,
                        member.mtime,
                        member.mode,
                    ))

    def test_unsafe_repository_entries_fail_closed(self) -> None:
        cases = {}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name in ("symlink", "hardlink", "reactor", "partial", "unknown"):
                repository = self._repository(base / name)
                cases[name] = repository
            symlink_target = cases["symlink"] / "org/example/demo/1.0/demo-1.0.jar"
            (symlink_target.parent / "linked.jar").symlink_to(symlink_target)
            hardlink_target = cases["hardlink"] / "org/example/demo/1.0/demo-1.0.jar"
            os.link(hardlink_target, hardlink_target.parent / "hard.jar")
            reactor = cases["reactor"] / "io/trino/trino-main/483"
            reactor.mkdir(parents=True)
            (reactor / "trino-main-483.pom").write_text("reactor", encoding="utf-8")
            (cases["partial"] / "download.lastUpdated").write_text(
                "partial", encoding="utf-8"
            )
            unknown = cases["unknown"] / "org/example/other/1.0"
            unknown.mkdir(parents=True)
            (unknown / "other.jar").write_bytes(b"unknown")
            (unknown / "_remote.repositories").write_text(
                "other.jar>sonatype-nexus-snapshots=\n",
                encoding="iso-8859-1",
            )
            for name, repository in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.build_manifest(repository)

    def test_noncanonical_manifest_types_and_archive_links_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            mutations = {
                "root-array": lambda value: value["files"],
                "boolean-schema": lambda value: {
                    **value,
                    "schema_version": True,
                },
                "boolean-size": lambda value: {
                    **value,
                    "files": [
                        {**value["files"][0], "size": True},
                        *value["files"][1:],
                    ],
                },
                "numeric-path": lambda value: {
                    **value,
                    "files": [
                        {**value["files"][0], "path": 1},
                        *value["files"][1:],
                    ],
                },
                "array-origin": lambda value: {
                    **value,
                    "files": [
                        {**value["files"][0], "repository_origin": []},
                        *value["files"][1:],
                    ],
                },
            }
            for name, mutate in mutations.items():
                malformed = root / f"{name}.json"
                malformed.write_bytes(package._manifest_bytes(mutate(manifest)))
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.verify_snapshot(malformed, archive, None)

            symlink = root / "linked.tar.gz"
            symlink.symlink_to(archive)
            hardlink = root / "hardlinked.tar.gz"
            os.link(archive, hardlink)
            for name, linked_archive in (
                ("symlink", symlink),
                ("hardlink", hardlink),
            ):
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.verify_snapshot(descriptor, linked_archive, None)

    def test_tampered_archive_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            tampered = root / "tampered.tar.gz"
            with tarfile.open(archive, "r:gz") as source:
                members = source.getmembers()
                payloads = {
                    member.name: source.extractfile(member).read()
                    for member in members
                }
            first = members[0]
            payloads[first.name] = b"x" * first.size
            with tampered.open("xb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=0
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
                    ) as target:
                        for member in members:
                            target.addfile(member, io.BytesIO(payloads[member.name]))
            with self.assertRaises(package.SnapshotError):
                package.verify_snapshot(descriptor, tampered, None)


class PublisherContractTests(unittest.TestCase):
    def test_repository_contract_and_workflow_are_closed(self) -> None:
        verify.audit(ROOT)

    def test_first_private_publication_requires_owner_visibility_bootstrap(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                "required_visibility": "public",
                "sign_and_attest_before_anonymous_pull": True,
                "owner_action_on_first_private_run": (
                    "set-package-public-and-rerun"
                ),
                "failed_attempt_admitted": False,
                "user_credential_fallback": False,
            },
            contract["snapshot"]["visibility_bootstrap"],
        )

    def test_maven_policy_isolated_from_upstream_project_configuration(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        preimages = {
            entry["path"]: entry["sha256"]
            for entry in contract["source"]["preimages"]
        }
        self.assertEqual(
            {
                ".mvn/extensions.xml",
                ".mvn/maven.config",
                ".mvn/jvm.config",
                ".mvn/settings.xml",
            },
            set(preimages).intersection(
                {
                    ".mvn/extensions.xml",
                    ".mvn/maven.config",
                    ".mvn/jvm.config",
                    ".mvn/settings.xml",
                }
            ),
        )
        policy_files = {
            entry["path"]: entry["sha256"]
            for entry in contract["policy_files"]
        }
        self.assertEqual(
            preimages[".mvn/jvm.config"],
            policy_files[verify.JVM_CONFIG_PATH.as_posix()],
        )
        self.assertIn("--workdir /policy", workflow)
        self.assertIn("--file /workspace/pom.xml", workflow)
        self.assertNotIn("--workdir /workspace", workflow)
        self.assertEqual(
            (
                verify.EXPECTED_RESOLUTION_COMMAND,
                verify.EXPECTED_RESOLUTION_COMMAND,
            ),
            verify._resolution_maven_commands(workflow),
        )

    def test_each_resolver_command_requires_the_docs_exclusion(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        exclusion = "              -pl '!:trino-docs' \\\n"
        self.assertEqual(2, workflow.count(exclusion))
        altered = (
            workflow.replace(exclusion, "", 1)
            + "\n# misleading occurrence: -pl '!:trino-docs'\n"
        )
        self.assertEqual(4, altered.count("-pl '!:trino-docs'"))
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_RESOLUTION_COMMAND",
        ):
            verify._validate_workflow(contract, altered)

    def test_each_resolver_command_ignores_transitive_repositories(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        option = "              --ignore-transitive-repositories \\\n"
        self.assertEqual(2, workflow.count(option))
        altered = (
            workflow.replace(option, "", 1)
            + "\n# misleading occurrence: --ignore-transitive-repositories\n"
        )
        self.assertEqual(4, altered.count("--ignore-transitive-repositories"))
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_RESOLUTION_COMMAND",
        ):
            verify._validate_workflow(contract, altered)

    def test_offline_workflow_command_is_bound_to_contract(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            contract["offline_rebuild"]["command"],
            verify._offline_maven_command(workflow),
        )
        offline_goal = "              clean install -DskipTests\n"
        self.assertEqual(1, workflow.count(offline_goal))
        altered = workflow.replace(
            offline_goal,
            "              clean package -DskipTests\n",
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_COMMAND",
        ):
            verify._validate_workflow(contract, altered)

    def test_builder_global_settings_allow_only_inert_defaults(
        self,
    ) -> None:
        namespace = "http://maven.apache.org/SETTINGS/1.2.0"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.xml"
            blocker_settings = (
                "  <mirrors>\n"
                "    <mirror>\n"
                "      <id>maven-default-http-blocker</id>\n"
                "      <mirrorOf>external:http:*</mirrorOf>\n"
                "      <name>Pseudo repository to mirror external "
                "repositories initially using HTTP.</name>\n"
                "      <url>http://0.0.0.0/</url>\n"
                "      <blocked>true</blocked>\n"
                "    </mirror>\n"
                "  </mirrors>\n"
            )
            safe_settings = (
                f'<settings xmlns="{namespace}">\n'
                "  <pluginGroups/>\n"
                "  <proxies/>\n"
                "  <servers/>\n"
                f"{blocker_settings}"
                "  <profiles/>\n"
                "</settings>\n"
            )
            path.write_text(safe_settings, encoding="utf-8")
            verify.audit_builder_settings(path)
            workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
            self.assertNotIn('"global_settings_active_sections": []', workflow)
            self.assertIn(
                '"mirror:maven-default-http-blocker"',
                workflow,
            )
            self.assertIn(
                '"empty-standard-containers-plus-exact-default-http-blocker"',
                workflow,
            )

            unsafe_children = (
                "<activeProfiles/>",
                "<localRepository>/tmp/repository</localRepository>",
                "<offline>false</offline>",
                "<servers><server><id>other</id></server></servers>",
                "<mirrors>unexpected</mirrors>",
                "<profiles/><profiles/>",
                '<servers enabled="true"/>',
                '<foreign:servers xmlns:foreign="urn:foreign"/>',
                "unexpected<servers/>",
                "<servers/>unexpected",
                "<mirrors><mirror><id>other</id></mirror></mirrors>",
                "<mirrors><mirror>"
                "<id>maven-default-http-blocker</id>"
                "<mirrorOf>external:http:*</mirrorOf>"
                "<name>Pseudo repository to mirror external repositories "
                "initially using HTTP.</name>"
                "<url>http://0.0.0.0/</url>"
                "<blocked>false</blocked>"
                "</mirror></mirrors>",
            )
            for child in unsafe_children:
                path.write_text(
                    f'<settings xmlns="{namespace}">{child}</settings>\n',
                    encoding="utf-8",
                )
                with self.subTest(child=child):
                    with self.assertRaises(verify.ContractError):
                        verify.audit_builder_settings(path)

            for unsafe_settings, error in (
                (
                    safe_settings.replace(blocker_settings, "  <mirrors/>\n"),
                    "default HTTP blocker differs",
                ),
                (
                    safe_settings.replace(blocker_settings, ""),
                    "global settings container set differs",
                ),
            ):
                path.write_text(unsafe_settings, encoding="utf-8")
                with self.subTest(unsafe_settings=unsafe_settings):
                    with self.assertRaisesRegex(verify.ContractError, error):
                        verify.audit_builder_settings(path)

    def test_slsa_v1_payload_binds_evidence_and_exact_oci_subject(self) -> None:
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(2, workflow.count("--type slsaprovenance1"))
        self.assertNotIn("--type slsaprovenance \\", workflow)
        for evidence in (
            "maven-dependency-manifest.json",
            "trino-maven-dependencies-483.tar.gz",
            "trino-maven-dependencies-483.cdx.json",
            "trivy-vulnerability.json",
            "offline-build.json",
            "independent-reconstruction.json",
            "toolchain.json",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(f'"file:{evidence}"', workflow)
        self.assertIn('"https://in-toto.io/Statement/v1"', workflow)
        self.assertIn('"https://slsa.dev/provenance/v1"', workflow)
        self.assertIn("statement.get(\"predicate\") == expected_predicate", workflow)
        self.assertIn('"digest": {"sha256": expected_digest}', workflow)

    def test_authorization_is_half_open_and_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_authorization(
            contract,
            at=dt.datetime(2026, 7, 22, 22, 43, 36, tzinfo=dt.timezone.utc),
        )
        verify._validate_authorization(
            contract,
            at=dt.datetime(2026, 8, 21, 22, 43, 35, tzinfo=dt.timezone.utc),
        )
        for instant in (
            dt.datetime(2026, 7, 22, 22, 43, 35, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 8, 21, 22, 43, 36, tzinfo=dt.timezone.utc),
        ):
            with self.subTest(instant=instant):
                with self.assertRaises(verify.ContractError):
                    verify._validate_authorization(contract, at=instant)

    def test_transfer_log_rejects_unknown_repositories_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transfer.log"
            path.write_text(
                "Downloading from central: "
                "https://repo.maven.apache.org/maven2/org/example/demo.pom\n"
                "Downloaded from confluent: "
                "https://packages.confluent.io/maven/io/confluent/demo.jar\n",
                encoding="utf-8",
            )
            verify.audit_transfer_log(path)
            for unsafe in (
                "https://repo1.maven.org/maven2/demo.jar",
                "https://user:secret@repo.maven.apache.org/maven2/demo.jar",
                "http://repo.maven.apache.org/maven2/demo.jar",
            ):
                path.write_text(f"Downloading from other: {unsafe}\n", encoding="utf-8")
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(verify.ContractError):
                        verify.audit_transfer_log(path)

    def test_settings_have_only_closed_central_fallback_mirror(self) -> None:
        verify._validate_settings(ROOT)
        settings = (ROOT / verify.SETTINGS_PATH).read_text(encoding="utf-8")
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(
            verify.EXPECTED_SETTINGS_POLICY,
            contract["dependency_resolution"]["settings_policy"],
        )
        self.assertEqual(1, settings.count("<mirror>"))
        for name, value in verify.EXPECTED_REPOSITORY_MIRROR:
            self.assertIn(f"<{name}>{value}</{name}>", settings)
        for forbidden in (
            "<server>",
            "<proxy>",
            "<username>",
            "<password>",
            "${env.",
            "${settings.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, settings)

    def test_settings_reject_central_fallback_mirror_drift(self) -> None:
        settings_path = ROOT / verify.SETTINGS_PATH
        original = settings_path.read_text(encoding="utf-8")
        mutations = (
            (
                "<mirrorOf>*,!central,!confluent</mirrorOf>",
                "<mirrorOf>*</mirrorOf>",
            ),
            (
                "<url>https://repo.maven.apache.org/maven2/</url>",
                "<url>https://oss.sonatype.org/content/repositories/snapshots/</url>",
            ),
            (
                "</mirrors>",
                """
    <mirror>
      <id>unexpected-fallback</id>
      <mirrorOf>*</mirrorOf>
      <name>Unexpected fallback</name>
      <url>https://repo.maven.apache.org/maven2/</url>
    </mirror>
  </mirrors>""",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / verify.SETTINGS_PATH
            target.parent.mkdir(parents=True)
            for old, new in mutations:
                target.write_text(original.replace(old, new, 1), encoding="utf-8")
                with self.subTest(old=old, new=new):
                    with self.assertRaises(verify.ContractError):
                        verify._validate_settings(root)


if __name__ == "__main__":
    unittest.main()
