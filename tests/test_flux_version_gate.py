"""The GitOps Make gate requires the pinned, warning-free Flux client."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FluxVersionGateTests(unittest.TestCase):
    def test_exact_client_version_only(self):
        cases = (
            ("flux: v2.9.2\n", "", 0, "v2.9.2", True),
            ("flux: v2.9.2\n", "warning: stale client\n", 0, "v2.9.2", False),
            ("flux: v2.9.2\nwarning: stale client\n", "", 0, "v2.9.2", False),
            ("flux: v2.9.2\nflux: v2.9.3\n", "", 0, "v2.9.2", False),
            ("flux: v2.9.2 extra\n", "", 0, "v2.9.2", False),
            ("flux: v2.9.2\n\n", "", 0, "v2.9.2", False),
            ("flux: v2.9.3\n", "", 0, "v2.9.3", False),
            ("flux: v2.9.2\n", "", 1, "v2.9.2", False),
        )
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "flux"
            for output, stderr, exit_code, version, accepted in cases:
                with self.subTest(output=output, stderr=stderr, exit_code=exit_code, version=version):
                    fake.write_text(
                        "#!/bin/sh\nprintf '%b' " + repr(output)
                        + "\nprintf '%b' " + repr(stderr)
                        + f" >&2\nexit {exit_code}\n",
                        encoding="utf-8",
                    )
                    fake.chmod(0o700)
                    result = subprocess.run(
                        ["make", "--no-print-directory", "-s", "flux-version-check", f"FLUX={fake}", f"FLUX_VERSION={version}"],
                        cwd=ROOT,
                        env={"PATH": os.environ["PATH"], "HOME": directory},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
