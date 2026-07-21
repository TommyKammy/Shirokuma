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

from scripts.verify_gitops_image_admission import (
    AdmissionError,
    validate_rendered_image_multiset,
)


ROOT = Path(__file__).resolve().parents[1]


class GitOpsBootstrapContractTests(unittest.TestCase):
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

    def test_gitops_bootstrap_preflights_all_secret_inputs_before_apply(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("gitops-bootstrap:", 1)[1].split("\ngitops-status:", 1)[0]
        apply_offset = target.index("apply -input=false -auto-approve")
        required_variables = (
            "GITHUB_TOKEN",
            "TF_VAR_seaweedfs_s3_operator_access_key",
            "TF_VAR_seaweedfs_s3_operator_secret_key",
            "TF_VAR_seaweedfs_s3_application_access_key",
            "TF_VAR_seaweedfs_s3_application_secret_key",
            "TF_VAR_polaris_postgresql_password",
            "TF_VAR_polaris_root_client_secret",
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
            "TF_VAR_seaweedfs_s3_application_access_key",
            "TF_VAR_seaweedfs_s3_application_secret_key",
            "TF_VAR_polaris_postgresql_password",
            "TF_VAR_polaris_root_client_secret",
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
