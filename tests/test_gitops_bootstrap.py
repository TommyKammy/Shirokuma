from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from scripts.verify_gitops_image_admission import (
    AdmissionError,
    validate_rendered_image_multiset,
)
from tests.gitops_render import (
    RenderError, RepositoryContext, Resource, _canonical_git_url, _is_local_git_source,
    _apply_kustomize_transform, _json_stream, _render_flux_kustomization, _run,
    _stage_helm_chart, _verified_tool, default_repository_context, load_yaml_text,
)


ROOT = Path(__file__).resolve().parents[1]


class GitOpsBootstrapContractTests(unittest.TestCase):
    def test_permanent_iceberg_jobs_use_dedicated_oauth_and_s3(self):
        directory = ROOT / 'deploy/gitops/iceberg/bootstrap'
        for db, filename, job_name, catalog, service, oauth in (
            ('sales', 'job.yaml', 'iceberg-table-bootstrap', 'shirokuma_l1', 'polaris', 'polaris-iceberg-bootstrap-credentials'),
            ('finance', 'finance-job.yaml', 'iceberg-finance-table-bootstrap', 'finance', 'polaris-finance', 'polaris-finance-iceberg-bootstrap-credentials'),
        ):
            job = load_yaml_text((directory / filename).read_text())[0]
            self.assertEqual(job['metadata']['name'], job_name)
            container = job['spec']['template']['spec']['containers'][0]
            self.assertEqual(container['image'], 'apache/polaris@sha256:74e6416c75f24563fc62f4ed52699d4fb872741b6a3e0af77fdc551b4ee72fce')
            self.assertEqual(container['args'][container['args'].index('--class-path') + 1], '/deployments/lib/main/*')
            source_index = container['args'].index('/opt/shirokuma/IcebergBootstrap.java')
            for setting in ('-Dshirokuma.catalog=' + catalog, '-Dshirokuma.namespace=smoke',
                            '-Dshirokuma.base-location=s3://shirokuma-lakehouse/databases/' + db):
                self.assertLess(container['args'].index(setting), source_index)
            env = {item['name']: item for item in container['env']}
            self.assertEqual(env['POLARIS_URI']['value'], 'http://' + service + '.shirokuma-dev.svc.cluster.local:8181')
            for name, key in (('POLARIS_CLIENT_ID', 'client_id'), ('POLARIS_CLIENT_SECRET', 'client_secret'), ('POLARIS_REALM', 'realm')):
                self.assertEqual(env[name]['valueFrom']['secretKeyRef'], {'name': oauth, 'key': key})
            for name, key in (('AWS_REGION', 'S3_REGION'), ('AWS_ACCESS_KEY_ID', 'AWS_ACCESS_KEY_ID'), ('AWS_SECRET_ACCESS_KEY', 'AWS_SECRET_ACCESS_KEY')):
                self.assertEqual(env[name]['valueFrom']['secretKeyRef'], {'name': 'seaweedfs-s3-' + db + '-credentials', 'key': key})
            self.assertNotIn('root-credentials', (directory / filename).read_text())
            policy = load_yaml_text((directory / ('networkpolicy.yaml' if db == 'sales' else 'finance-networkpolicy.yaml')).read_text())[0]
            self.assertEqual(policy['spec']['podSelector']['matchLabels']['app.kubernetes.io/name'], job_name)
            self.assertEqual(policy['spec']['egress'][0]['to'][0]['podSelector']['matchLabels']['app.kubernetes.io/name'], service)
        source = (directory / 'IcebergBootstrap.java').read_text()
        run = source.split('private static Result run(', 1)[1].split('private static Map', 1)[0]
        self.assertNotIn('ensureManagementCatalog', run)
        self.assertNotIn('createNamespace', run)
        self.assertIn('operator must precreate', run)
        child = load_yaml_text((ROOT / 'deploy/gitops/clusters/local-lite/iceberg-bootstrap.yaml').read_text())[0]
        self.assertEqual(child['spec']['sourceRef'], {'kind': 'Bucket', 'name': 'shirokuma-local-source'})
        self.assertEqual({item['name'] for item in child['spec']['healthChecks']}, {'iceberg-table-bootstrap', 'iceberg-finance-table-bootstrap'})
        customization = load_yaml_text((directory / 'kustomization.yaml').read_text())[0]
        self.assertTrue({'job.yaml', 'finance-job.yaml', 'networkpolicy.yaml', 'finance-networkpolicy.yaml'} <= set(customization['resources']))
        rendered = subprocess.run(['rtk', 'proxy', 'kubectl', 'kustomize', str(directory)],
                                  text=True, capture_output=True, check=True)
        objects = load_yaml_text(rendered.stdout)
        jobs = [obj for obj in objects if obj['kind'] == 'Job']
        self.assertEqual({job['metadata']['name'] for job in jobs}, {'iceberg-table-bootstrap', 'iceberg-finance-table-bootstrap'})
        configmaps = {obj['metadata']['name'] for obj in objects if obj['kind'] == 'ConfigMap'}
        for job in jobs:
            self.assertIn(job['spec']['template']['spec']['volumes'][0]['configMap']['name'], configmaps)

    def test_reconcile_refuses_other_context_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "flux-fixture"
            calls = Path(directory) / "calls"
            binary.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = version ]; then printf "flux: v2.9.2\\n"; exit 0; fi\n'
                'printf "%s\\n" "$*" >> "$FLUX_FIXTURE_CALLS"\n',
                encoding="utf-8",
            )
            binary.chmod(0o755)
            env = {**os.environ, "FLUX_FIXTURE_CALLS": str(calls)}
            result = subprocess.run(
                ["make", "--no-print-directory", "gitops-reconcile",
                 "KUBE_CONTEXT=colima-other", f"FLUX={binary}"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(calls.exists(), "reconcile must not run before scope checks")
            self.assertIn("dedicated Kubernetes context", result.stdout)
            dry_run = subprocess.run(
                ["make", "-n", "--no-print-directory", "gitops-reconcile",
                 "KUBE_CONTEXT=colima-mac-studio-solo", f"FLUX={binary}"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertLess(dry_run.stdout.index("./scripts/colima_baseline.sh status"),
                            dry_run.stdout.index("reconcile source git"))
            self.assertFalse(calls.exists())
            stopped = Path(directory) / "colima-stopped"
            stopped.write_text('#!/bin/sh\nexit 1\n', encoding="utf-8")
            stopped.chmod(0o755)
            failed_baseline = subprocess.run(
                ["make", "--no-print-directory", "gitops-reconcile",
                 "KUBE_CONTEXT=colima-mac-studio-solo", f"FLUX={binary}"],
                cwd=ROOT, env={**env, "COLIMA_BIN": str(stopped)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(failed_baseline.returncode, 0)
            self.assertFalse(calls.exists(), "failed baseline must not reconcile")

    def test_renderer_refuses_duplicate_json_keys_at_any_depth(self) -> None:
        for output in (
            '{"kind":"ConfigMap","kind":"Secret"}',
            '{"kind":"List","items":[{"kind":"ConfigMap","metadata":{"name":"a","name":"b"}}]}',
            '{"kind":"ConfigMap","k\\u0069nd":"Secret"}',
        ):
            with self.subTest(output=output), self.assertRaisesRegex(RenderError, "duplicate JSON key"):
                _json_stream(output)
        self.assertEqual(_json_stream('{"kind":"ConfigMap"}\n{"kind":"Secret"}'),
                         [{"kind": "ConfigMap"}, {"kind": "Secret"}])

    def test_kubectl_version_refuses_duplicate_keys_and_redacts_untrusted_version(self) -> None:
        _verified_tool.cache_clear()
        for output in (
            '{"clientVersion":{"gitVersion":"v1.36.2","gitVersion":"v1.36.2"},"kustomizeVersion":"v5.8.1"}',
            json.dumps({"clientVersion": {"gitVersion": "private-credential-marker"},
                        "kustomizeVersion": "v5.8.1"}),
        ):
            with self.subTest(output=output), patch(
                "tests.gitops_render._tool", return_value="kubectl-fixture"
            ), patch("tests.gitops_render._run", return_value=output):
                with self.assertRaises(RenderError) as caught:
                    _verified_tool("kubectl")
                self.assertNotIn("private-credential-marker", str(caught.exception))
        _verified_tool.cache_clear()

    def test_nonempty_yaml_refuses_empty_kubectl_output(self) -> None:
        for output in ("", " \n\t"):
            with self.subTest(output=output), patch(
                "tests.gitops_render._verified_tool", return_value="kubectl-fixture"
            ), patch("tests.gitops_render._run", return_value=output), self.assertRaisesRegex(
                RenderError, "no resources"
            ):
                load_yaml_text("kind: ConfigMap\n")
        with patch("tests.gitops_render._verified_tool", return_value="kubectl-fixture"), patch(
            "tests.gitops_render._run", return_value='{"kind":"ConfigMap"}'
        ):
            self.assertEqual(load_yaml_text("kind: ConfigMap\n"), [{"kind": "ConfigMap"}])
        self.assertEqual(load_yaml_text(""), [])

    def test_kubectl_version_requires_object(self) -> None:
        _verified_tool.cache_clear()
        for output in ("[]", "null", '"version"'):
            with self.subTest(output=output), patch(
                "tests.gitops_render._tool", return_value="kubectl-fixture"
            ), patch("tests.gitops_render._run", return_value=output), self.assertRaisesRegex(
                RenderError, "non-object version"
            ):
                _verified_tool("kubectl")
        _verified_tool.cache_clear()

    def test_renderer_refuses_tool_launch_failure_without_raw_exception(self) -> None:
        with patch("tests.gitops_render.subprocess.run", side_effect=OSError("executable vanished")):
            with self.assertRaisesRegex(RenderError, "renderer command could not start"):
                _run(("fixture-tool", "version"))

    def test_renderer_failure_does_not_echo_tool_output_or_arguments(self) -> None:
        marker = "private-credential-marker"
        with patch("tests.gitops_render.subprocess.run", return_value=subprocess.CompletedProcess(
            args=[], returncode=7, stdout=marker, stderr=marker
        )):
            with self.assertRaises(RenderError) as caught:
                _run(("fixture-tool", marker))
        self.assertNotIn(marker, str(caught.exception))
        self.assertIn("exit status 7", str(caught.exception))
        for failure in (subprocess.TimeoutExpired(["fixture-tool", marker], 60), None):
            with self.subTest(failure=failure), patch(
                "tests.gitops_render.subprocess.run",
                side_effect=failure,
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="ok", stderr=marker
                ),
            ), self.assertRaises(RenderError) as caught:
                _run(("fixture-tool", marker), reject_stderr=True)
            self.assertNotIn(marker, str(caught.exception))

    def test_kubectl_version_refuses_warning_on_success(self) -> None:
        _verified_tool.cache_clear()
        version = json.dumps({"clientVersion": {"gitVersion": "v1.36.2"}, "kustomizeVersion": "v5.8.1"})
        with patch("tests.gitops_render._tool", return_value="kubectl-fixture"), patch(
            "tests.gitops_render.subprocess.run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=version, stderr="warning: unexpected client settings\n"
            )
        ), self.assertRaises(RenderError):
            _verified_tool("kubectl")
        _verified_tool.cache_clear()

    def test_kubectl_resource_conversion_refuses_warning(self) -> None:
        with patch("tests.gitops_render._verified_tool", return_value="kubectl-fixture"), patch(
            "tests.gitops_render.subprocess.run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"kind":"ConfigMap"}',
                stderr="warning: conversion may be incomplete\n",
            ),
        ), self.assertRaisesRegex(RenderError, "emitted stderr"):
            load_yaml_text("kind: ConfigMap\n")

    def test_kustomize_transform_refuses_success_with_warning(self) -> None:
        with patch("tests.gitops_render._verified_tool", return_value="kubectl-fixture"), patch(
            "tests.gitops_render.subprocess.run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="kind: ConfigMap\n", stderr="private-warning-marker\n",
            ),
        ), self.assertRaises(RenderError) as caught:
            _apply_kustomize_transform("kind: ConfigMap\n", {"namespace": "test"})
        self.assertNotIn("private-warning-marker", str(caught.exception))

    def test_flux_build_refuses_success_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "base"
            target.mkdir()
            context = RepositoryContext.create(root, ["ssh://git@example.invalid/team/repo.git"])
            resource = Resource(root / "flux.yaml", {
                "kind": "Kustomization", "metadata": {"name": "test"},
                "spec": {"path": "./base"},
            })
            with patch("tests.gitops_render._validate_flux_build_inputs"), patch(
                "tests.gitops_render._verified_tool", return_value="flux-fixture"
            ), patch("tests.gitops_render.load_yaml_text", return_value=[{"kind": "ConfigMap", "apiVersion": "v1"}]), patch("tests.gitops_render.subprocess.run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="kind: ConfigMap\n", stderr="private-warning-marker\n",
            )), self.assertRaises(RenderError) as caught:
                _render_flux_kustomization(resource, context, "")
            self.assertNotIn("private-warning-marker", str(caught.exception))

    def test_helm_package_refuses_success_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / "chart"
            chart.mkdir()
            staging = root / "staging"
            staging.mkdir()
            with patch("tests.gitops_render._verified_tool", return_value="helm-fixture"), patch(
                "tests.gitops_render.subprocess.run", return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="packaged", stderr="private-warning-marker\n",
                ),
            ), self.assertRaisesRegex(RenderError, "emitted stderr") as caught:
                _stage_helm_chart(chart, staging)
            self.assertNotIn("private-warning-marker", str(caught.exception))

    def test_renderer_refuses_malformed_resource_list_instead_of_dropping_it(self) -> None:
        for items in (None, {}, "hidden", 1):
            with self.subTest(items=items), self.assertRaises(RenderError):
                _json_stream(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}))
        self.assertEqual(
            _json_stream(json.dumps({"kind": "List", "items": [{"kind": "ConfigMap"}]})),
            [{"kind": "ConfigMap"}],
        )

    def test_renderer_refuses_unbounded_nested_lists(self) -> None:
        nested: dict[str, object] = {"kind": "ConfigMap"}
        for _ in range(20):
            nested = {"kind": "List", "items": [nested]}
        with self.assertRaisesRegex(RenderError, "nested List"):
            _json_stream(json.dumps(nested))
        self.assertEqual(_json_stream('{"kind":"List","items":[{"kind":"List","items":[{"kind":"ConfigMap"}]}]}'),
                         [{"kind": "ConfigMap"}])

    def test_renderer_refuses_partial_resource_lists(self) -> None:
        for metadata in (
            {"continue": "next-page"},
            {"remainingItemCount": 1},
            {"continue": "next-page", "remainingItemCount": 0},
            {"remainingItemCount": True},
            {"continue": None},
        ):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(RenderError, "incomplete List"):
                _json_stream(json.dumps({"kind": "List", "items": [{"kind": "ConfigMap"}], "metadata": metadata}))
        self.assertEqual(
            _json_stream(json.dumps({"kind": "List", "items": [{"kind": "ConfigMap"}],
                                     "metadata": {"continue": "", "remainingItemCount": 0}})),
            [{"kind": "ConfigMap"}],
        )
        with self.assertRaisesRegex(RenderError, "incomplete List"):
            _json_stream(json.dumps({"kind": "List", "items": [
                {"kind": "List", "metadata": {"continue": "next-page"},
                 "items": [{"kind": "ConfigMap"}]}
            ]}))

    def test_helm_renderer_refuses_ambiguous_version_output(self) -> None:
        expected = "v4.2.2+gb05881c\n"
        for output in (
            expected + "v4.2.3+other\n",
            "v4.2.2+\n",
            " v4.2.2+gb05881c\n",
        ):
            with self.subTest(output=output):
                _verified_tool.cache_clear()
                with patch("tests.gitops_render._tool", return_value="helm-fixture"), patch(
                    "tests.gitops_render._run", return_value=output
                ):
                    with self.assertRaises(RenderError):
                        _verified_tool("helm")
        _verified_tool.cache_clear()
        with patch("tests.gitops_render._tool", return_value="helm-fixture"), patch(
            "tests.gitops_render._run", return_value=expected
        ) as run:
            self.assertEqual(_verified_tool("helm"), "helm-fixture")
            run.assert_called_once_with(("helm-fixture", "version", "--short"), reject_stderr=True)
        _verified_tool.cache_clear()
        with patch("tests.gitops_render._tool", return_value="helm-fixture"), patch(
            "tests.gitops_render.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=expected, stderr="warning: renderer changed\n"
            ),
        ), self.assertRaises(RenderError):
            _verified_tool("helm")
        _verified_tool.cache_clear()

    def test_repository_context_refuses_ambiguous_source_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url = "ssh://git@example.invalid/team/repo.git"
            for urls, branches, refs in (
                ((url, url), ("main", "main"), ("first", "second")),
                ((url, "git@example.invalid:team/repo.git"), ("main", "main"), ("first", "second")),
            ):
                with self.subTest(urls=urls), self.assertRaises(RenderError):
                    RepositoryContext.create(root, urls, branches, refs)

    def test_checked_in_context_refuses_incomplete_git_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sync = root / "gitops/clusters/local-lite/flux-system/gotk-sync.yaml"
            sync.parent.mkdir(parents=True)
            sync.write_text("fixture", encoding="utf-8")
            valid = {
                "kind": "GitRepository", "apiVersion": "source.toolkit.fluxcd.io/v1",
                "spec": {"url": "ssh://git@example.invalid/team/repo.git", "ref": {"branch": "main"}},
            }
            incomplete = {
                "kind": "GitRepository", "apiVersion": "source.toolkit.fluxcd.io/v1",
                "spec": {"url": "ssh://git@example.invalid/other.git", "ref": {}},
            }
            with patch("tests.gitops_render.load_yaml_file", return_value=[valid, incomplete]):
                with self.assertRaises(RenderError):
                    default_repository_context(root, root)

    def test_checked_in_context_refuses_competing_git_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sync = root / "gitops/clusters/local-lite/flux-system/gotk-sync.yaml"
            sync.parent.mkdir(parents=True)
            sync.write_text("fixture", encoding="utf-8")
            valid = {
                "kind": "GitRepository", "apiVersion": "source.toolkit.fluxcd.io/v1",
                "spec": {"url": "ssh://git@example.invalid/team/repo.git", "ref": {"branch": "main"}},
            }
            with patch("tests.gitops_render.load_yaml_file", return_value=[valid]):
                self.assertEqual(1, len(default_repository_context(root, root).local_git_sources))
            for ref in ({"branch": "main", "tag": "release"}, {"branch": "main", "commit": "a" * 40}, {"branch": "main", "semver": "*"}):
                with self.subTest(ref=ref), patch("tests.gitops_render.load_yaml_file", return_value=[
                    {**valid, "spec": {**valid["spec"], "ref": ref}},
                ]), self.assertRaises(RenderError):
                    default_repository_context(root, root)

    def test_git_source_invalid_authority_is_scoped_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = RepositoryContext.create(root, ["ssh://git@example.invalid/team/repo.git"])
            for url in ("ssh://[broken/team/repo.git", "ssh://git@host:bad/team/repo.git"):
                with self.subTest(url=url):
                    source = Resource(root / "source.yaml", {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "GitRepository", "metadata": {"name": "source"},
                        "spec": {"url": url, "ref": {"branch": "main"}},
                    })
                    self.assertFalse(_is_local_git_source(source, context))
            with self.assertRaises(RenderError) as caught:
                _canonical_git_url("ssh://git:private-marker@[broken/team/repo.git")
            self.assertNotIn("private-marker", str(caught.exception))

    def test_local_source_rejects_unmodeled_fetch_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = RepositoryContext.create(root, ["ssh://git@example.invalid/team/repo.git"])
            spec = {"url": "ssh://git@example.invalid/team/repo.git", "ref": {"branch": "main"}}
            source = lambda value: Resource(root / "source.yaml", {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository", "metadata": {"name": "source"}, "spec": value,
            })
            self.assertTrue(_is_local_git_source(source(spec), context))
            for field, value in (
                ("proxySecretRef", {"name": "alternate-proxy"}),
                ("certSecretRef", {"name": "alternate-ca"}),
                ("provider", "azure"),
                ("insecure", True),
            ):
                with self.subTest(field=field), self.assertRaises(RenderError):
                    _is_local_git_source(source({**spec, field: value}), context)

    def test_checked_in_source_credential_reference_is_not_interchangeable(self) -> None:
        context = default_repository_context(ROOT, ROOT / "deploy")
        source = Resource(
            ROOT / "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {"name": "flux-system", "namespace": "flux-system"},
                "spec": {
                    "url": "ssh://git@github.com/TommyKammy/Shirokuma",
                    "ref": {"branch": "main"},
                    "secretRef": {"name": "flux-system"},
                },
            },
        )
        self.assertTrue(_is_local_git_source(source, context))
        for secret in (None, {"name": "alternate"}, {"name": "flux-system", "extra": True}):
            with self.subTest(secret=secret), self.assertRaises(RenderError):
                altered = {**source.value, "spec": {**source.value["spec"]}}
                if secret is None:
                    altered["spec"].pop("secretRef")
                else:
                    altered["spec"]["secretRef"] = secret
                _is_local_git_source(Resource(source.path, altered), context)

    def test_repository_owns_flux_bootstrap_entrypoints(self) -> None:
        required_files = (
            "opentofu/dev/main.tf",
            "opentofu/dev/variables.tf",
            "opentofu/dev/versions.tf",
            "opentofu/dev/bootstrap-images.json",
            "bootstrap/flux/v2.9.2/README.md",
            "bootstrap/flux/v2.9.2/components.json",
            "deploy/gitops/dev/kustomization.yaml",
            "deploy/gitops/dev/smoke-configmap.yaml",
            "deploy/gitops/clusters/local-lite/dev.yaml",
            "deploy/gitops/clusters/local-lite/kustomization.yaml",
            "deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml",
            "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml",
            "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml",
            "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml",
        )
        missing = [path for path in required_files if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"missing Flux bootstrap paths: {missing}")

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target in (
            "tofu-fmt:",
            "tofu-validate:",
            "flux-version-check:",
            "gitops-bootstrap:",
            "gitops-status:",
            "gitops-reconcile:",
            "gitops-teardown:",
        ):
            with self.subTest(target=target):
                self.assertIn(target, makefile)

    def test_flux_distribution_and_controller_images_are_pinned(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        candidates = json.loads(
            (ROOT / "opentofu/dev/bootstrap-images.json").read_text(encoding="utf-8")
        )
        inventory = json.loads(
            (ROOT / "bootstrap/flux/v2.9.2/components.json").read_text(encoding="utf-8")
        )
        customization = (
            ROOT / "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("FLUX_VERSION ?= v2.9.2", makefile)
        self.assertEqual(inventory["flux_version"], "v2.9.2")
        self.assertEqual(
            set(candidates),
            {
                "source-controller",
                "kustomize-controller",
                "helm-controller",
                "notification-controller",
            },
        )
        inventory_by_name = {item["name"]: item for item in inventory["components"]}
        self.assertEqual(set(inventory_by_name), set(candidates))
        for name, candidate in candidates.items():
            with self.subTest(component=name):
                self.assertRegex(candidate["reference"], r"^ghcr\.io/fluxcd/.+@sha256:[0-9a-f]{64}$")
                self.assertEqual(inventory_by_name[name]["reference"], candidate["reference"])
                self.assertEqual(inventory_by_name[name]["version"], candidate["version"])
                self.assertIn(f"value: {candidate['reference']}", customization)
                self.assertIn(f"name: {name}", customization)

    def test_root_kustomization_is_the_only_apply_path_for_smoke_state(self) -> None:
        dev = (ROOT / "deploy/gitops/dev/kustomization.yaml").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("- smoke-configmap.yaml", dev)
        sync = (ROOT / "deploy/gitops/clusters/local-lite/dev.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("path: ./deploy/gitops/dev", sync)
        self.assertNotIn("kubectl apply", makefile)
        flux_sync = (
            ROOT / "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml"
        ).read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^kind: GitRepository$", flux_sync)), 1)
        self.assertEqual(len(re.findall(r"(?m)^kind: Kustomization$", flux_sync)), 1)
        self.assertIn("branch: main", flux_sync)
        self.assertIn("path: ./deploy/gitops/clusters/local-lite", flux_sync)
        self.assertIn("prune: true", flux_sync)
        self.assertIn("secretRef:\n    name: flux-system", flux_sync)

    def test_gitops_commands_are_reproducible_and_noninteractive(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("init -backend=false -input=false -lockfile=readonly", makefile)
        self.assertIn("apply -input=false -auto-approve", makefile)
        self.assertIn("destroy -input=false -auto-approve", makefile)
        self.assertIn("bootstrap github", makefile)
        self.assertNotRegex(makefile, r"bootstrap github[^\n]*--silent")
        self.assertIn("--components=source-controller,kustomize-controller,helm-controller,notification-controller", makefile)
        self.assertIn("GITHUB_TOKEN is required", makefile)
        self.assertIn("FLUX_GITHUB_REPOSITORY ?= Shirokuma", makefile)
        self.assertIn("FLUX_GITHUB_PRIVATE ?= false", makefile)
        self.assertIn("FLUX_BOOTSTRAP_BRANCH ?= flux/bootstrap-local-lite", makefile)
        self.assertNotRegex(makefile, r"(?m)^GIT_BRANCH \?= main$")
        self.assertNotRegex(makefile, r"(?m)^GITHUB_REPOSITORY \?=")
        self.assertIn("--repository=$(FLUX_GITHUB_REPOSITORY)", makefile)
        self.assertIn("--private=$(FLUX_GITHUB_PRIVATE)", makefile)
        self.assertIn("--branch=$(FLUX_BOOTSTRAP_BRANCH)", makefile)
        self.assertLess(
            makefile.index("GITHUB_TOKEN is required"),
            makefile.index("apply -input=false -auto-approve"),
        )
        self.assertIn(
            "reconcile kustomization shirokuma-dev -n flux-system",
            makefile,
        )
        self.assertIn(
            "reconcile kustomization shirokuma-object-storage -n flux-system",
            makefile,
        )
        self.assertIn("flux-system", makefile)

    def test_historical_mutating_targets_refuse_before_preflights(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target, next_target, reason in (
            ("gitops-bootstrap", "gitops-status", "GitHub write"),
            ("gitops-teardown", "colima-start", "retained data"),
        ):
            with self.subTest(target=target):
                recipe = makefile.split(f"\n{target}:", 1)[1].split(
                    f"\n{next_target}:", 1
                )[0]
                first_command = next(line for line in recipe.splitlines() if line.startswith("\t"))
                self.assertIn(reason, first_command)
                self.assertIn("exit 1", first_command)
                self.assertEqual(makefile.split(f"\n{target}:", 1)[1].splitlines()[0], "")
                result = subprocess.run(
                    ["make", "--no-print-directory", target, "TOFU=/bin/false", "FLUX=/bin/false"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stdout)
                self.assertNotIn("No rule to make target", result.stderr)

    def test_gitops_status_rejects_foreign_context_before_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls"
            for executable in ("kubectl", "flux"):
                binary = root / executable
                binary.write_text('#!/bin/sh\nprintf "called\\n" >> "$CALLS_FILE"\n', encoding="utf-8")
                binary.chmod(0o755)
            result = subprocess.run(
                ["make", "--no-print-directory", "gitops-status", "KUBE_CONTEXT=colima-other",
                 f"FLUX={root / 'flux'}"],
                cwd=ROOT,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "CALLS_FILE": str(calls)},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dedicated Kubernetes context", result.stdout)
            self.assertFalse(calls.exists())

    def test_gitops_bootstrap_preflights_all_secret_inputs_before_apply(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("gitops-bootstrap:", 1)[1].split("\ngitops-status:", 1)[0]
        apply_offset = target.index("apply -input=false -auto-approve")
        required_variables = (
            "GITHUB_TOKEN",
            "TF_VAR_seaweedfs_s3_operator_access_key",
            "TF_VAR_seaweedfs_s3_operator_secret_key",
            "TF_VAR_polaris_postgresql_password",
            "TF_VAR_polaris_database_credentials",
            "TF_VAR_local_gitops_s3_credentials",
        )

        for variable in required_variables:
            with self.subTest(variable=variable):
                reference = f"$${{{variable}:-}}"
                self.assertIn(reference, target)
                self.assertLess(target.index(reference), apply_offset)
                self.assertNotIn(f'echo "$${{{variable}}}"', target)

        legacy_lookup = target.index('legacy_pvc="$$(kubectl')
        self.assertLess(legacy_lookup, apply_offset)
        self.assertIn(
            "-n shirokuma-dev get pvc seaweedfs-data-seaweedfs-0 "
            "--ignore-not-found -o name",
            target,
        )
        self.assertIn("legacy PVC lookup failed; refusing OpenTofu apply", target)
        self.assertIn("verified export and the whole-profile nuke/rebuild", target)

    def test_legacy_pvc_preflight_distinguishes_absence_presence_and_lookup_failure(
        self,
    ) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("gitops-bootstrap:", 1)[1].split(
            "\ngitops-status:", 1
        )[0]
        recipe_line = next(line for line in target.splitlines() if "legacy_pvc=" in line)
        command = (
            recipe_line.strip().removeprefix("@").replace("$$", "$").replace(
                "$(KUBE_CONTEXT)", "fixture-context"
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_kubectl = root / "kubectl"
            args_file = root / "kubectl.args"
            fake_kubectl.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" > "$KUBECTL_ARGS_FILE"\n'
                'printf "%s" "${KUBECTL_OUTPUT:-}"\n'
                'exit "${KUBECTL_EXIT:-0}"\n',
                encoding="utf-8",
            )
            fake_kubectl.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "KUBECTL_ARGS_FILE": str(args_file),
            }

            absent = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            present = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                env={
                    **environment,
                    "KUBECTL_OUTPUT": "persistentvolumeclaim/seaweedfs-data-seaweedfs-0",
                },
                check=False,
            )
            failed = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                env={**environment, "KUBECTL_EXIT": "7"},
                check=False,
            )

            self.assertEqual(absent.returncode, 0, absent.stdout + absent.stderr)
            self.assertNotEqual(present.returncode, 0)
            self.assertIn("whole-profile nuke/rebuild", present.stdout)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("lookup failed", failed.stdout)
            self.assertEqual(
                args_file.read_text(encoding="utf-8").strip(),
                "--context fixture-context -n shirokuma-dev get pvc "
                "seaweedfs-data-seaweedfs-0 --ignore-not-found -o name",
            )

    def test_object_storage_reconcile_is_resource_aware_and_fail_closed(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("gitops-reconcile:", 1)[1].split(
            "\ngitops-teardown:", 1
        )[0]
        recipe_line = next(
            line for line in target.splitlines() if "object_storage=" in line
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_kubectl = root / "kubectl"
            fake_flux = root / "flux"
            flux_args = root / "flux.args"
            fake_kubectl.write_text(
                "#!/bin/sh\n"
                'printf "%s" "${KUBECTL_OUTPUT:-}"\n'
                'exit "${KUBECTL_EXIT:-0}"\n',
                encoding="utf-8",
            )
            fake_flux.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" > "$FLUX_ARGS_FILE"\n',
                encoding="utf-8",
            )
            fake_kubectl.chmod(0o700)
            fake_flux.chmod(0o700)
            command = (
                recipe_line.strip()
                .removeprefix("@")
                .replace("$$", "$")
                .replace("$(KUBE_CONTEXT)", "fixture-context")
                .replace("$(FLUX)", str(fake_flux))
            )
            environment = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "FLUX_ARGS_FILE": str(flux_args),
            }

            absent = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(absent.returncode, 0, absent.stdout + absent.stderr)
            self.assertIn("absent; skipping reconcile", absent.stdout)
            self.assertFalse(flux_args.exists())

            present = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                env={
                    **environment,
                    "KUBECTL_OUTPUT": (
                        "kustomization.kustomize.toolkit.fluxcd.io/"
                        "shirokuma-object-storage"
                    ),
                },
                check=False,
            )
            self.assertEqual(present.returncode, 0, present.stdout + present.stderr)
            self.assertEqual(
                flux_args.read_text(encoding="utf-8").strip(),
                "reconcile kustomization shirokuma-object-storage -n flux-system "
                "--context=fixture-context",
            )
            flux_args.unlink()

            failed = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                env={**environment, "KUBECTL_EXIT": "9"},
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("lookup failed", failed.stdout)
            self.assertFalse(flux_args.exists())

    def test_gitops_teardown_preflights_destroy_before_flux_uninstall(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("gitops-teardown:", 1)[1].split(
            "\ncolima-start:", 1
        )[0]
        plan_offset = target.index("plan -destroy -refresh=false -input=false")
        uninstall_offset = target.index("uninstall --context=$(KUBE_CONTEXT)")
        destroy_offset = target.index("destroy -input=false -auto-approve")
        required_variables = (
            "TF_VAR_seaweedfs_s3_operator_access_key",
            "TF_VAR_seaweedfs_s3_operator_secret_key",
            "TF_VAR_polaris_postgresql_password",
            "TF_VAR_polaris_database_credentials",
            "TF_VAR_local_gitops_s3_credentials",
        )

        for variable in required_variables:
            with self.subTest(variable=variable):
                reference = f"$${{{variable}:-}}"
                self.assertIn(reference, target)
                self.assertLess(target.index(reference), plan_offset)
                self.assertNotIn(f'echo "$${{{variable}}}"', target)
        self.assertLess(plan_offset, uninstall_offset)
        self.assertLess(uninstall_offset, destroy_offset)

    ADMISSION_FIXTURES = {
        "candidates": "opentofu/dev/bootstrap-images.json",
        "inventory": "bootstrap/flux/v2.9.2/components.json",
        "ledger": "security/resident-images.json",
        "customization": (
            "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml"
        ),
        "components": (
            "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
        ),
        "sync": "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml",
    }

    @classmethod
    def admission_fixture(cls, name: str) -> str:
        return (ROOT / cls.ADMISSION_FIXTURES[name]).read_text(encoding="utf-8")

    def run_image_admission(
        self,
        overrides: dict[str, str] | None = None,
        *,
        symlink_components: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        overrides = overrides or {}
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            paths: dict[str, Path] = {}
            for name, source in self.ADMISSION_FIXTURES.items():
                destination = temporary_root / Path(source).name
                content = overrides.get(name, self.admission_fixture(name))
                if name == "components" and symlink_components:
                    target = temporary_root / "components-target.yaml"
                    target.write_text(content, encoding="utf-8")
                    destination.symlink_to(target)
                else:
                    destination.write_text(content, encoding="utf-8")
                paths[name] = destination
            arguments = [sys.executable, str(ROOT / "scripts/verify_gitops_image_admission.py")]
            for name in self.ADMISSION_FIXTURES:
                arguments.extend((f"--{name}", str(paths[name])))
            return subprocess.run(
                arguments,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def run_kustomize(
        self, overrides: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for name in ("customization", "components", "sync"):
                source = self.ADMISSION_FIXTURES[name]
                (temporary_root / Path(source).name).write_text(
                    overrides.get(name, self.admission_fixture(name)),
                    encoding="utf-8",
                )
            return subprocess.run(
                ["kubectl", "kustomize", str(temporary_root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    @staticmethod
    def mutate_deployment(
        content: str, name: str, replacement: Callable[[str], str]
    ) -> str:
        matches = list(re.finditer(r"(?ms)^---\n.*?(?=^---\n|\Z)", content))
        selected = [
            match
            for match in matches
            if "\nkind: Deployment\n" in match.group(0)
            and f"\n  name: {name}\n" in match.group(0)
        ]
        if len(selected) != 1:
            raise AssertionError(f"expected one generated Deployment for {name}")
        match = selected[0]
        document = match.group(0)
        return content[: match.start()] + replacement(document) + content[match.end() :]

    def test_committed_generated_flux_bootstrap_is_admitted(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/verify_gitops_image_admission.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok images=4", result.stdout)

    def test_unadmitted_bootstrap_image_fails_closed(self) -> None:
        ledger = json.loads(self.admission_fixture("ledger"))
        ledger["images"] = [
            image
            for image in ledger["images"]
            if image.get("component") != "source-controller"
        ]
        result = self.run_image_admission({"ledger": json.dumps(ledger)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires exactly one component entry", result.stdout)

    def test_generated_components_reject_controller_image_and_container_drift(self) -> None:
        components = self.admission_fixture("components")
        source = json.loads(self.admission_fixture("candidates"))["source-controller"]
        source_image = f"{source['repository']}:{source['version']}"
        mutations = {
            "missing": self.mutate_deployment(components, "source-controller", lambda _: ""),
            "duplicate": self.mutate_deployment(
                components, "source-controller", lambda document: document + document
            ),
            "unexpected": self.mutate_deployment(
                components,
                "source-controller",
                lambda document: document
                + document.replace(
                    "  name: source-controller\n",
                    "  name: image-reflector-controller\n",
                    1,
                ),
            ),
            "repository": self.mutate_deployment(
                components,
                "source-controller",
                lambda document: document.replace(
                    source_image, "registry.example.invalid/evil:v1", 1
                ),
            ),
            "version": self.mutate_deployment(
                components,
                "source-controller",
                lambda document: document.replace(source_image, f"{source['repository']}:v9", 1),
            ),
            "sidecar": self.mutate_deployment(
                components,
                "source-controller",
                lambda document: document.replace(
                    "        name: manager\n",
                    "        name: manager\n"
                    "      - image: registry.example.invalid/sidecar:v1\n"
                    "        name: sidecar\n",
                    1,
                ),
            ),
            "duplicate-image": self.mutate_deployment(
                components,
                "source-controller",
                lambda document: document.replace(
                    f"        image: {source_image}\n",
                    f"        image: {source_image}\n"
                    f"        image: {source_image}\n",
                    1,
                ),
            ),
            "init": self.mutate_deployment(
                components,
                "source-controller",
                lambda document: document.replace(
                    "      containers:\n",
                    "      initContainers:\n"
                    "      - image: registry.example.invalid/init:v1\n"
                    "        name: init\n"
                    "      containers:\n",
                    1,
                ),
            ),
        }
        for label, mutated in mutations.items():
            with self.subTest(label=label):
                result = self.run_image_admission({"components": mutated})
                self.assertNotEqual(result.returncode, 0)

    def test_generated_components_reject_unicode_escaped_sidecar_block_item(self) -> None:
        components = self.admission_fixture("components")
        mutated = self.mutate_deployment(
            components,
            "source-controller",
            lambda document: document.replace(
                "        name: manager\n",
                "        name: manager\n"
                "      -\n"
                '        "im\\u0061ge": registry.example.invalid/sidecar:v1\n'
                '        "na\\u006de": sidecar\n',
                1,
            ),
        )
        rendered = self.run_kustomize({"components": mutated})
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        self.assertIn("registry.example.invalid/sidecar:v1", rendered.stdout)
        result = self.run_image_admission({"components": mutated})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical byte SHA-256", result.stdout)

    def test_customization_rejects_quoted_duplicate_patches_key(self) -> None:
        customization = self.admission_fixture("customization")
        mutated = (
            customization
            + '"patches":\n'
            + "  - patch: |-\n"
            + "      "
            + '[{"op":"replace","path":"/spec/template/spec/containers/0/image",'
            + '"value":"registry.example.invalid/evil:v1"}]\n'
            + "    target:\n"
            + "      kind: Deployment\n"
            + "      name: source-controller\n"
        )
        rendered = self.run_kustomize({"customization": mutated})
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        self.assertIn("registry.example.invalid/evil:v1", rendered.stdout)
        result = self.run_image_admission({"customization": mutated})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical byte SHA-256", result.stdout)

    def test_rendered_images_must_be_exact_admitted_multiset(self) -> None:
        candidates = json.loads(self.admission_fixture("candidates"))
        rendered_images = [
            candidate["reference"] for candidate in candidates.values()
        ] + ["registry.example.invalid/evil@sha256:" + "a" * 64]
        with self.assertRaisesRegex(AdmissionError, "rendered image multiset"):
            validate_rendered_image_multiset(
                rendered_images,
                [candidate["reference"] for candidate in candidates.values()],
            )

    def test_candidates_and_digest_patches_reject_drift_or_ambiguity(self) -> None:
        candidates = json.loads(self.admission_fixture("candidates"))
        customization = self.admission_fixture("customization")
        first_patch = customization.index("  - patch: |")
        second_patch = customization.index("  - patch: |", first_patch + 1)
        patch_block = customization[first_patch:second_patch]
        cases = {
            "candidate-version": {
                "candidates": json.dumps(
                    {
                        **candidates,
                        "source-controller": {
                            **candidates["source-controller"],
                            "version": "v9.9.9",
                        },
                    }
                )
            },
            "candidate-repository": {
                "candidates": json.dumps(
                    {
                        **candidates,
                        "source-controller": {
                            **candidates["source-controller"],
                            "repository": "registry.example.invalid/evil",
                        },
                    }
                )
            },
            "candidate-tag": {
                "candidates": json.dumps(
                    {
                        **candidates,
                        "source-controller": {
                            **candidates["source-controller"],
                            "tag": "v9.9.9@sha256:" + "a" * 64,
                        },
                    }
                )
            },
            "missing-patch": {
                "customization": customization[:first_patch] + customization[second_patch:]
            },
            "duplicate-patch": {"customization": customization + patch_block},
            "unexpected-patch": {
                "customization": customization
                + patch_block.replace("name: source-controller", "name: image-reflector-controller")
            },
            "unexpected-transformer": {
                "customization": customization
                + "images:\n"
                + "  - name: ghcr.io/fluxcd/source-controller\n"
                + "    newTag: latest\n"
            },
            "uninterpretable-components": {
                "components": self.mutate_deployment(
                    self.admission_fixture("components"),
                    "source-controller",
                    lambda document: document.replace(
                        "kind: Deployment", "kind: [Deployment]", 1
                    ),
                )
            },
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                result = self.run_image_admission(overrides)
                self.assertNotEqual(result.returncode, 0)
        symlink = self.run_image_admission(symlink_components=True)
        self.assertNotEqual(symlink.returncode, 0)
        self.assertIn("symbolic link", symlink.stdout)

    def test_sync_manifest_rejects_branch_path_prune_secret_and_shape_drift(self) -> None:
        sync = self.admission_fixture("sync")
        mutations = (
            sync.replace("branch: main", "branch: flux/bootstrap-local-lite"),
            sync.replace(
                "path: ./deploy/gitops/clusters/local-lite", "path: ./deploy/gitops/dev"
            ),
            sync.replace("prune: true", "prune: false"),
            sync.replace("secretRef:\n    name: flux-system", "secretRef: {}"),
            sync + "---\napiVersion: v1\nkind: Secret\nmetadata:\n  name: extra\n",
        )
        for mutated in mutations:
            with self.subTest(sync=mutated):
                result = self.run_image_admission({"sync": mutated})
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
