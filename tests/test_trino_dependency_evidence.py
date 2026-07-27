from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trino_dependency_evidence as evidence  # noqa: E402


class TrinoDependencyEvidenceTests(unittest.TestCase):
    def test_retained_evidence_is_closed_and_structurally_valid(self) -> None:
        evidence.audit(ROOT, cryptographic=False)

    def test_sigstore_reverification_uses_exact_identity_and_bundles(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            evidence.subprocess,
            "run",
            return_value=completed,
        ) as run:
            evidence.reverify_sigstore(ROOT)
        self.assertEqual(2, run.call_count)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual("verify-blob", commands[0][1])
        self.assertEqual("verify-blob-attestation", commands[1][1])
        for command in commands:
            self.assertIn(evidence.IDENTITY, command)
            self.assertIn(evidence.ISSUER, command)
            self.assertIn(evidence.SOURCE_SHA, command)

    def test_tampered_retained_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "bootstrap", root / "bootstrap")
            (root / ".github/workflows").mkdir(parents=True)
            publication = (
                root / evidence.EVIDENCE_DIR / "anonymous-pull.json"
            )
            document = json.loads(publication.read_text(encoding="utf-8"))
            document["result"] = "failed"
            publication.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(evidence.EvidenceError, "INVENTORY"):
                evidence.audit(root, cryptographic=False)

    def test_retired_publisher_reintroduction_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "bootstrap", root / "bootstrap")
            (root / ".github/workflows").mkdir(parents=True)
            (root / evidence.ACTIVE_WORKFLOW_PATH).write_text(
                "name: forbidden\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(evidence.EvidenceError, "PUBLISHER"):
                evidence.audit(root, cryptographic=False)


if __name__ == "__main__":
    unittest.main()
