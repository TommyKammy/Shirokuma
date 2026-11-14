"""CLI dispatch and output ownership without optional SQL dependencies."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import __main__ as cli
from benchmarks.local_perf.capacity import Campaign


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.campaign = Campaign.create(self.root / "campaign")
        self.modules = {}
        for suite in ("tpch", "tpcds"):
            module = ModuleType(f"benchmarks.local_perf.{suite}_sql")
            setattr(module, f"prepare_{suite}", Mock(return_value=self.root / "prepared.json"))
            module.bind_snapshots = Mock(return_value={"bound": suite})
            self.modules[module.__name__] = module
        modules_patch = patch.dict(sys.modules, self.modules)
        modules_patch.start()
        self.addCleanup(modules_patch.stop)

    def invoke(self, *args, code=0):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["local-perf", *map(str, args)]), redirect_stdout(out), redirect_stderr(err):
            if code:
                with self.assertRaises(SystemExit) as caught:
                    cli.main()
                self.assertEqual(caught.exception.code, code)
            else:
                cli.main()
        return out.getvalue(), err.getvalue()

    def module(self, suite):
        return self.modules[f"benchmarks.local_perf.{suite}_sql"]

    def test_load_public_batch_size_dispatch_and_argument_validation(self):
        prepared = self.root / "prepared.json"
        ingest = ModuleType("benchmarks.local_perf.ingest")
        load = ingest.load_public = Mock(return_value="loaded")
        with patch.dict(sys.modules, {ingest.__name__: ingest}), \
                patch.object(cli, "adapter") as adapter:
            for flags, expected in (([], 2000), (["--batch-size", "500"], 500),
                                    (["--batch-size", "1"], 1), (["--batch-size", "10000"], 10000)):
                with self.subTest(flags=flags):
                    load.reset_mock()
                    adapter.reset_mock()
                    out, err = self.invoke("load-public", self.campaign.root, prepared, "load",
                                           "--adapter", "public", *flags)
                    self.assertEqual(json.loads(out), "loaded")
                    self.assertEqual(err, "")
                    args, kwargs = load.call_args
                    self.assertEqual(load.call_count, 1)
                    self.assertEqual(args[0].root, self.campaign.root)
                    self.assertEqual(args[1:], (str(prepared), "load", adapter.return_value))
                    self.assertEqual(kwargs, {"batch_size": expected})
            for value in ("0", "-1", "10001", "1.5", "invalid"):
                with self.subTest(value=value):
                    load.reset_mock()
                    adapter.reset_mock()
                    _, err = self.invoke("load-public", self.campaign.root, prepared, "load",
                                         "--adapter", "public", "--batch-size", value, code=2)
                    self.assertIn("--batch-size", err)
                    load.assert_not_called()
                    adapter.assert_not_called()

    def test_prepare_dispatch_and_tpch_defaults_preserved(self):
        dataset = self.root / "dataset.json"
        for suite, flags, expected in (("tpch", [], {}), ("tpcds", [], {"timeout": 300}),
                                       ("tpcds", ["--timeout", "12.5"], {"timeout": 12.5})):
            with self.subTest(suite=suite, flags=flags):
                prepare = getattr(self.module(suite), f"prepare_{suite}")
                prepare.reset_mock()
                out, err = self.invoke(f"prepare-{suite}", self.campaign.root, dataset, "plan", *flags)
                self.assertEqual(json.loads(out), str(self.root / "prepared.json"))
                self.assertEqual(err, "")
                args, kwargs = prepare.call_args
                self.assertEqual(prepare.call_count, 1)
                self.assertEqual(args[0].root, self.campaign.root)
                self.assertEqual(args[1:], (str(dataset), "plan"))
                self.assertEqual(kwargs, expected)

    def test_bind_dispatch_and_existing_output_rejected(self):
        manifest, pins = {"queries": ["query"]}, {"tables": ["table"]}
        source, snapshots = self.root / "plan.json", self.root / "pins.json"
        source.write_text(json.dumps(manifest))
        snapshots.write_text(json.dumps(pins))
        for suite in ("tpch", "tpcds"):
            with self.subTest(suite=suite):
                target = self.root / f"{suite}.json"
                bind = self.module(suite).bind_snapshots
                out, err = self.invoke(f"bind-{suite}", source, snapshots, target)
                bind.assert_called_once_with(manifest, pins)
                self.assertEqual(json.loads(out), {"bound": suite})
                self.assertEqual(json.loads(target.read_text()), {"bound": suite})
                self.assertEqual(err, "")
                before = target.read_bytes()
                bind.reset_mock()
                _, err = self.invoke(f"bind-{suite}", source, snapshots, target, code=1)
                self.assertIn("ValueError", err)
                bind.assert_not_called()
                self.assertEqual(target.read_bytes(), before)

    def test_bind_outside_repo_guard_and_exclusive_creation(self):
        source = self.root / "source.json"
        source.write_text("{}")
        repo = Path(cli.__file__).resolve().parents[2]
        for suite in ("tpch", "tpcds"):
            bind = self.module(suite).bind_snapshots
            for target in (repo, repo / "cli-must-not-create.json"):
                with self.subTest(suite=suite, target=target):
                    _, err = self.invoke(f"bind-{suite}", source, source, target, code=1)
                    self.assertIn("ValueError", err)
                    bind.assert_not_called()
            target = self.root / f"racing-{suite}.json"

            def concurrent_writer(*args):
                target.write_text("other writer\n")
                return {"bound": suite}

            bind.side_effect = concurrent_writer
            _, err = self.invoke(f"bind-{suite}", source, source, target, code=1)
            self.assertIn("FileExistsError", err)
            self.assertEqual(target.read_text(), "other writer\n")

    def test_argument_errors_before_prepare(self):
        for flags in ([], [str(self.campaign.root), "dataset", "plan", "--timeout", "0"],
                      [str(self.campaign.root), "dataset", "plan", "--timeout", "-1"],
                      [str(self.campaign.root), "dataset", "plan", "--timeout", "nan"],
                      [str(self.campaign.root), "dataset", "plan", "--timeout", "inf"],
                      [str(self.campaign.root), "dataset", "plan", "--timeout", "invalid"]):
            with self.subTest(flags=flags):
                _, err = self.invoke("prepare-tpcds", *flags, code=2)
                self.assertIn("error:", err)
        self.module("tpcds").prepare_tpcds.assert_not_called()
        _, err = self.invoke("bind-tpcds", "plan", "pins", code=2)
        self.assertIn("output", err)

    def test_module_help_entrypoint(self):
        repo = Path(cli.__file__).resolve().parents[2]
        for command, expected in (([], ["prepare-tpcds", "bind-tpcds"]),
                                  (["prepare-tpcds"], ["campaign", "input", "name", "--timeout"]),
                                  (["bind-tpcds"], ["plan_manifest", "pins", "output"])):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, "-m", "benchmarks.local_perf", *command, "--help"],
                                        cwd=repo, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                for text in expected:
                    self.assertIn(text, result.stdout)


if __name__ == "__main__":
    unittest.main()
