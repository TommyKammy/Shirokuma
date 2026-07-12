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
            "bootstrap/flux/v2.9.1/gotk-components.yaml",
            "bootstrap/flux/v2.9.1/gotk-sync.yaml",
            "bootstrap/flux/v2.9.1/kustomization.yaml",
            "deploy/gitops/dev/kustomization.yaml",
            "deploy/gitops/dev/smoke-configmap.yaml",
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
        components = (ROOT / "bootstrap/flux/v2.9.1/gotk-components.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("FLUX_VERSION ?= v2.9.1", makefile)
        self.assertEqual(
            set(candidates),
            {
                "source-controller",
                "kustomize-controller",
                "helm-controller",
                "notification-controller",
            },
        )
        for name, candidate in candidates.items():
            with self.subTest(component=name):
                self.assertRegex(candidate["reference"], r"^ghcr\.io/fluxcd/.+@sha256:[0-9a-f]{64}$")
                self.assertIn(candidate["reference"], components)
        self.assertNotRegex(components, r"image: ghcr\.io/fluxcd/[^\s]+:v[0-9]")
        self.assertIn(
            "# Components: source-controller,kustomize-controller,helm-controller,notification-controller",
            components,
        )
        self.assertEqual(components.count("kind: Deployment\n"), 4)

    def test_root_kustomization_is_the_only_apply_path_for_smoke_state(self) -> None:
        sync = (ROOT / "bootstrap/flux/v2.9.1/gotk-sync.yaml").read_text(
            encoding="utf-8"
        )
        dev = (ROOT / "deploy/gitops/dev/kustomization.yaml").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("kind: GitRepository", sync)
        self.assertIn("kind: Kustomization", sync)
        self.assertIn("path: ./deploy/gitops/clusters/local-lite", sync)
        self.assertIn("prune: true", sync)
        self.assertIn("wait: true", sync)
        self.assertIn("- smoke-configmap.yaml", dev)
        self.assertNotIn("kubectl apply", makefile)
        self.assertFalse((ROOT / "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml").exists())

    def test_gitops_commands_are_reproducible_and_noninteractive(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("init -backend=false -input=false -lockfile=readonly", makefile)
        self.assertIn("apply -input=false -auto-approve", makefile)
        self.assertIn("destroy -input=false -auto-approve", makefile)
        self.assertIn("bootstrap github", makefile)
        self.assertIn("--components=source-controller,kustomize-controller,helm-controller,notification-controller", makefile)
        self.assertIn("GITHUB_TOKEN is required", makefile)
        self.assertIn("flux-system", makefile)

    def run_image_admission(
        self, candidates: dict[str, object], ledger: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            candidates_path = temporary_root / "candidates.json"
            ledger_path = temporary_root / "ledger.json"
            candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify_gitops_image_admission.py"),
                    "--candidates",
                    str(candidates_path),
                    "--ledger",
                    str(ledger_path),
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
