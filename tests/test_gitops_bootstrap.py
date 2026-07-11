from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitOpsBootstrapContractTests(unittest.TestCase):
    def test_repository_owns_declarative_bootstrap_entrypoints(self) -> None:
        required_files = (
            "opentofu/dev/main.tf",
            "opentofu/dev/variables.tf",
            "opentofu/dev/versions.tf",
            "charts/dev-root/Chart.yaml",
            "charts/dev-root/templates/application.yaml",
            "deploy/gitops/dev/smoke-configmap.yaml",
        )
        missing = [path for path in required_files if not (ROOT / path).is_file()]

        self.assertEqual(missing, [], f"missing GitOps bootstrap paths: {missing}")

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target in ("tofu-fmt:", "tofu-validate:", "gitops-bootstrap:", "gitops-teardown:"):
            with self.subTest(target=target):
                self.assertIn(target, makefile)

    def test_bootstrap_dependencies_and_workload_images_are_pinned(self) -> None:
        versions = (ROOT / "opentofu/dev/versions.tf").read_text(encoding="utf-8")
        main = (ROOT / "opentofu/dev/main.tf").read_text(encoding="utf-8")
        images = (ROOT / "opentofu/dev/bootstrap-images.json").read_text(
            encoding="utf-8"
        )

        self.assertIn('required_version = "= 1.12.3"', versions)
        self.assertIn('version = "3.2.0"', versions)
        self.assertIn('version = "3.2.1"', versions)
        self.assertRegex(main, r'version\s*=\s*"10[.]1[.]3"')
        self.assertGreaterEqual(len(re.findall(r"@sha256:[0-9a-f]{64}", images)), 4)
        self.assertIn("jsondecode", main)
        self.assertIn("enabled = false", main, "Dex must remain disabled in the local baseline")

    def test_root_application_is_the_only_apply_path_for_smoke_state(self) -> None:
        application = (ROOT / "charts/dev-root/templates/application.yaml").read_text(
            encoding="utf-8"
        )
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("automated:", application)
        self.assertIn("prune: true", application)
        self.assertIn("selfHeal: true", application)
        self.assertNotIn("kubectl apply", makefile)

    def test_gitops_commands_are_reproducible_and_noninteractive(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("init -backend=false -input=false -lockfile=readonly", makefile)
        self.assertIn("apply -input=false -auto-approve", makefile)
        self.assertIn("destroy -input=false -auto-approve", makefile)
        self.assertIn("kubectl config set-context --current --namespace=argocd", makefile)
        self.assertIn(
            'KUBECONFIG="$$kubeconfig" argocd app list --core --kube-context $(KUBE_CONTEXT)',
            makefile,
        )

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
                text=True,
                capture_output=True,
                check=False,
            )

    def test_unadmitted_bootstrap_images_fail_closed(self) -> None:
        digest = "a" * 64
        reference = f"registry.example.com/shirokuma/argocd@sha256:{digest}"
        result = self.run_image_admission(
            {
                "argocd": {
                    "repository": "registry.example.com/shirokuma/argocd",
                    "tag": f"v1.0.0@sha256:{digest}",
                    "reference": reference,
                }
            },
            {"images": []},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not admitted", result.stdout)

    def test_admission_checks_the_image_actually_deployed(self) -> None:
        digest = "b" * 64
        admitted_reference = f"registry.example.com/trusted/argocd@sha256:{digest}"
        result = self.run_image_admission(
            {
                "argocd": {
                    "repository": "registry.example.com/untrusted/argocd",
                    "tag": f"v1.0.0@sha256:{digest}",
                    "reference": admitted_reference,
                }
            },
            {"images": [{"reference": admitted_reference}]},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match deployed image", result.stdout)

    def test_matching_deployed_image_can_be_admitted(self) -> None:
        digest = "c" * 64
        reference = f"registry.example.com/shirokuma/argocd@sha256:{digest}"
        result = self.run_image_admission(
            {
                "argocd": {
                    "repository": "registry.example.com/shirokuma/argocd",
                    "tag": f"v1.0.0@sha256:{digest}",
                    "reference": reference,
                }
            },
            {"images": [{"reference": reference}]},
        )

        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
