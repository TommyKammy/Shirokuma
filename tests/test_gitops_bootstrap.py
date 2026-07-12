from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIGEST = "a" * 64


class GitOpsBootstrapContractTests(unittest.TestCase):
    def test_repository_owns_flux_bootstrap_entrypoints(self) -> None:
        required_files = (
            "opentofu/dev/main.tf",
            "opentofu/dev/variables.tf",
            "opentofu/dev/versions.tf",
            "opentofu/dev/bootstrap-images.json",
            "bootstrap/flux/v2.9.1/README.md",
            "bootstrap/flux/v2.9.1/components.json",
            "deploy/gitops/dev/kustomization.yaml",
            "deploy/gitops/dev/smoke-configmap.yaml",
            "deploy/gitops/clusters/local-lite/dev.yaml",
            "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml",
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
            (ROOT / "bootstrap/flux/v2.9.1/components.json").read_text(encoding="utf-8")
        )
        customization = (
            ROOT / "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("FLUX_VERSION ?= v2.9.1", makefile)
        self.assertEqual(inventory["flux_version"], "v2.9.1")
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
        self.assertFalse((ROOT / "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml").exists())

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
        self.assertIn("flux-system", makefile)

    def run_image_admission(
        self,
        candidates: dict[str, object],
        ledger: dict[str, object],
        customized_candidates: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            candidates_path = temporary_root / "candidates.json"
            ledger_path = temporary_root / "ledger.json"
            customization_path = temporary_root / "kustomization.yaml"
            candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            customization_path.write_text(
                self.customization(customized_candidates or candidates), encoding="utf-8"
            )
            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify_gitops_image_admission.py"),
                    "--candidates",
                    str(candidates_path),
                    "--ledger",
                    str(ledger_path),
                    "--customization",
                    str(customization_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    @staticmethod
    def candidate(repository: str, digest: str = DIGEST) -> dict[str, str]:
        return {
            "repository": repository,
            "tag": f"v1.0.0@sha256:{digest}",
            "reference": f"{repository}@sha256:{digest}",
        }

    @staticmethod
    def customization(candidates: dict[str, object]) -> str:
        patches = []
        for component, candidate in candidates.items():
            assert isinstance(candidate, dict)
            patches.append(
                "  - patch: |\n"
                "      - op: replace\n"
                "        path: /spec/template/spec/containers/0/image\n"
                f"        value: {candidate['reference']}\n"
                "    target:\n"
                "      kind: Deployment\n"
                f"      name: {component}\n"
            )
        return "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\npatches:\n" + "".join(patches)

    def test_unadmitted_bootstrap_images_fail_closed(self) -> None:
        candidate = self.candidate("registry.example.com/shirokuma/source-controller")
        result = self.run_image_admission(
            {"source-controller": candidate},
            {"schema_version": 1, "images": []},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not admitted", result.stdout)

    def test_admission_checks_the_image_actually_deployed(self) -> None:
        admitted = self.candidate("registry.example.com/trusted/source-controller")
        deployed = self.candidate("registry.example.com/untrusted/source-controller")
        result = self.run_image_admission(
            {"source-controller": deployed},
            {"schema_version": 1, "images": [{"reference": admitted["reference"]}]},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not admitted", result.stdout)

    def test_admission_checks_the_bootstrap_customization(self) -> None:
        admitted = self.candidate("registry.example.com/trusted/source-controller")
        customized = self.candidate("registry.example.com/untrusted/source-controller")
        result = self.run_image_admission(
            {"source-controller": admitted},
            {"schema_version": 1, "images": [{"reference": admitted["reference"]}]},
            {"source-controller": customized},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap customization deploys", result.stdout)

    def test_matching_deployed_image_can_be_admitted(self) -> None:
        candidate = self.candidate("registry.example.com/shirokuma/source-controller")
        result = self.run_image_admission(
            {"source-controller": candidate},
            {"schema_version": 1, "images": [{"reference": candidate["reference"]}]},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok images=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
