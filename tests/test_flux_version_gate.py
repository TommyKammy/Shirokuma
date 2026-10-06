"""The GitOps Make gate must not accept ambiguous Flux client output."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FluxVersionGateTests(unittest.TestCase):
    def test_exact_client_version_only(self):
        cases = (
            ("flux: v2.9.2\n", 0, True),
            ("flux: v2.9.2\nwarning: stale client\n", 0, False),
            ("flux: v2.9.2\nflux: v2.9.3\n", 0, False),
            ("flux: v2.9.2 extra\n", 0, False),
            ("flux: v2.9.2\n\n", 0, False),
            ("flux: v2.9.3\n", 0, False),
            ("flux: v2.9.2\n", 1, False),
        )
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "flux"
            for output, exit_code, accepted in cases:
                with self.subTest(output=output, exit_code=exit_code):
                    fake.write_text(
                        "#!/bin/sh\nprintf '%b' " + repr(output) + f"\nexit {exit_code}\n",
                        encoding="utf-8",
                    )
                    fake.chmod(0o700)
                    result = subprocess.run(
                        ["make", "--no-print-directory", "-s", "flux-version-check", f"FLUX={fake}"],
                        cwd=ROOT,
                        env={"PATH": os.environ["PATH"], "HOME": directory},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
