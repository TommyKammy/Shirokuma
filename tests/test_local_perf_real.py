"""Explicit extension smoke only. Ordinary core suite never installs extensions."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.dataset import generate, prepare_reference, qualify_packaged_tpch
from benchmarks.local_perf.runner import DuckDBAdapter, run


@unittest.skipUnless(os.environ.get("LOCAL_PERF_REAL") == "1", "explicit dedicated-venv extension qualification only")
class RealTPCQualification(unittest.TestCase):
    def test_tpch_22_parquet_reference_qualification(self):
        # Keep evidence outside Git even on failure, and print its exact location.
        root = Path(tempfile.mkdtemp(prefix="shirokuma-perf-real-")).resolve()
        print("local-perf real output:", root, flush=True)
        campaign = Campaign.create(root / "campaign")
        dataset = generate(campaign, "tpch", scale=.01)
        meta = json.loads((dataset / "dataset.json").read_text())
        self.assertEqual(meta["generator_version"], "1.4.3")
        self.assertEqual(len(meta["tables"]), 8)
        self.assertEqual(len(meta["queries"]), 22)
        reference = prepare_reference(campaign, dataset, "reference")
        known = qualify_packaged_tpch(campaign, dataset, reference, "packaged-answers")
        self.assertEqual(json.loads((known / "qualification.json").read_text())["status"], "SUCCESS")
        manifest = json.loads((dataset / "queries.json").read_text())
        result = run(campaign, "qualification", manifest, DuckDBAdapter(dataset), references=reference, scale=.01)
        report = json.loads((result / "run.json").read_text())
        self.assertEqual(report["status"], "SUCCESS")
        self.assertEqual(len(report["attempts"]), 22)
        self.assertTrue(all(a["status"] == "SUCCESS" for a in report["attempts"]))
        print(json.dumps({"tables": len(meta["tables"]), "queries": len(meta["queries"]),
                          "parquet_bytes": meta["parquet_physical_bytes"], "qualification": report["status"],
                          "oracle": "same-engine roundtrip, not independent", "output": str(root)}), flush=True)


if __name__ == "__main__":
    unittest.main()
