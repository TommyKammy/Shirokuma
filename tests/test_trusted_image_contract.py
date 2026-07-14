from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trusted_image as verifier  # noqa: E402


STATIC_PATHS = (
    Path(".github/workflows/seaweedfs-arm64.yml"),
    Path("bootstrap/seaweedfs/v4.39/Containerfile"),
    Path("bootstrap/seaweedfs/v4.39/admission.json"),
    Path("bootstrap/seaweedfs/v4.39/source.json"),
    Path("bootstrap/seaweedfs/v4.39/trusted-build-contract.json"),
)


class TrustedImageContractTests(unittest.TestCase):
    def _copy_static_tree(self, destination: Path) -> None:
        for relative in STATIC_PATHS:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def _assert_mutation_fails(
        self,
        mutate: Callable[[Path], None],
        expected_code: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_static_tree(root)
            mutate(root)
            with self.assertRaises(verifier.ContractError) as caught:
                verifier.validate_static_contract(root)
            self.assertEqual(caught.exception.code, expected_code)

    def test_repository_static_contract_is_closed_and_valid(self) -> None:
        contract = verifier.validate_static_contract(ROOT)
        self.assertEqual(
            set(contract["toolchain"]),
            {"buildx", "buildkit", "syft", "trivy", "cosign", "crane"},
        )
        workflow = (ROOT / contract["workflow"]["path"]).read_text(encoding="utf-8")
        self.assertIn("needs: verify", workflow)
        self.assertNotIn("imjasonh/setup-crane@", workflow)
        self.assertNotIn("docker/setup-buildx-action@", workflow)

    def test_contract_mutations_fail_with_stable_error_codes(self) -> None:
        def remove_buildkit(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            del data["toolchain"]["buildkit"]
            path.write_text(json.dumps(data), encoding="utf-8")

        def alter_containerfile(root: Path) -> None:
            path = root / "bootstrap/seaweedfs/v4.39/Containerfile"
            path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

        def unpin_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            workflow = workflow.replace(
                "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10",
                "actions/checkout@v6",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def remove_promotion_dependency(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    needs: verify\n",
                "",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def permit_runtime(root: Path) -> None:
            path = root / verifier.ADMISSION_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["runtime_manifests"]["permitted"] = True
            path.write_text(json.dumps(data), encoding="utf-8")

        cases = (
            (remove_buildkit, "TOOLCHAIN_CLOSED_WORLD"),
            (alter_containerfile, "CONTAINERFILE_HASH"),
            (unpin_action, "ACTION_NOT_SHA_PINNED"),
            (remove_promotion_dependency, "PROMOTION_DEPENDENCY"),
            (permit_runtime, "ADMISSION_RUNTIME_STATE"),
        )
        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self._assert_mutation_fails(mutate, expected_code)


if __name__ == "__main__":
    unittest.main()
