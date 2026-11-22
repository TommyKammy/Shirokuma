import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class VerifyShardTests(unittest.TestCase):
    """CI's Verify matrix must cover exactly what `make verify` runs."""

    def test_ci_matrix_matches_the_make_verify_shards(self):
        makefile = (ROOT / "Makefile").read_text()
        verify = re.search(r"^verify: (.+)$", makefile, re.M).group(1).split()
        self.assertEqual(verify, ["test-resident-core", "verify-static"])
        shards = re.search(r"^test-resident-core: (.+)$", makefile, re.M).group(1).split()
        for shard in shards:
            self.assertRegex(makefile, rf"(?m)^{re.escape(shard)}:\n\t")
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        matrix = workflow["jobs"]["verify"]["strategy"]["matrix"]["target"]
        self.assertEqual(matrix, ["verify-static", *shards])
        self.assertFalse(workflow["jobs"]["verify"]["strategy"]["fail-fast"])
        self.assertEqual(workflow["jobs"]["verify-summary"]["needs"], "verify")


if __name__ == "__main__":
    unittest.main()
