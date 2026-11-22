import datetime as dt
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest

from benchmarks.local_perf.capacity import Campaign, allocated, guest_requirement
from benchmarks.local_perf.protocol import GIB, LIMIT, plan, protocol, settings, validate
from benchmarks.local_perf.results import Writer, compare
from benchmarks.local_perf.runner import select_only
from unittest.mock import patch
from tests.local_perf_host import setUpModule, tearDownModule  # noqa: F401


class ProtocolTests(unittest.TestCase):
    def test_profiles_are_plans_and_bounded(self):
        for name in protocol()["profiles"]:
            p = plan(name)
            self.assertFalse(p["applies_changes"])
            self.assertGreaterEqual(p["remaining_host_ram_gib"], 0)
            self.assertGreater(p["remaining_vm_ram_gib"], 0)
        p = protocol()
        p["physical_additional_limit_bytes"] = 3_000_000_000_000
        with self.assertRaises(ValueError):
            validate(p)
        self.assertEqual(settings()["scale"], .01)
        for kwargs in ({"mode": "performance"}, {"scale": 100}, {"scale": float("nan")},
                       {"peak": 1}, {"repetitions": 101}, {"scale": -1}):
            with self.assertRaises(ValueError):
                settings(**kwargs)
        self.assertEqual(settings("performance", 100, 600_000_000_000, 900, 5)["logical_bytes"], 100_000_000_000)

    def test_guest_capacity_is_not_host_capacity(self):
        report = guest_requirement(400 * GIB, 157018820608, 200 * GIB)
        self.assertFalse(report["ready"])
        self.assertEqual(report["reserve_bytes"], 64 * GIB)
        self.assertEqual(guest_requirement(100 * GIB, 20 * GIB, 1, root=True)["reserve_bytes"], 10 * GIB)

    def test_select_default(self):
        select_only("-- hi\nSELECT 'drop table' AS x;")
        select_only("WITH x AS (SELECT 1) SELECT * FROM x")
        for sql in ("SELECT 1; DROP TABLE t", "COPY t TO 'x'", "WITH x AS (DELETE FROM t) SELECT 1"):
            with self.assertRaises(ValueError):
                select_only(sql)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.image = self.base / "vm.img"
        with self.image.open("wb") as f:
            f.truncate(10 * GIB)
        self.c = Campaign.create(self.base / "campaign", images=[self.image], drain_bytes=1024)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, delta=0, free=3_000_000_000_000):
        current = dict(self.c.state["baseline"])
        current[str(self.image)] += delta
        return dict(allocated=current, host_free=free)

    def test_sparse_and_stable_baseline(self):
        self.assertLess(allocated(self.image), self.image.stat().st_size)
        baseline = dict(self.c.state["baseline"])
        first = self.c.own("d1", "dataset")
        self.c.mark(first, "SUCCESS")
        self.c.cleanup("d1")
        reopened = Campaign(self.c.root)
        self.assertEqual(reopened.state["baseline"], baseline)
        self.assertEqual(reopened.check(snapshot=self.snapshot(10000))["used_bytes"], 10000)
        with self.assertRaises(FileExistsError):
            self.c.own("d1", "dataset")
        with self.assertRaises(ValueError):
            self.c.cleanup("unowned")

    def test_scandir_live_add_delete_sparse_hardlink_and_symlink(self):
        tree = self.base / "tree"
        tree.mkdir()
        target = tree / "target"
        target.write_bytes(b"x" * 8192)
        (tree / "dir").mkdir()
        child = tree / "dir" / "child"
        child.write_bytes(b"y" * 4096)
        os.link(target, tree / "hard")
        (tree / "file-link").symlink_to(target)
        (tree / "dir-link").symlink_to(tree / "dir")
        expected = allocated(target) + allocated(child)
        self.assertEqual(allocated(tree), expected)

        added = tree / "added"
        added.write_bytes(b"z" * 4096)
        self.assertGreater(allocated(tree), expected)
        added.unlink()
        self.assertEqual(allocated(tree), expected)

        a, b = self.base / "a", self.base / "b"
        a.mkdir(); b.mkdir()
        campaign = Campaign.create(self.base / "cross-root", host_paths=[a, b])
        initial = campaign.check()["used_bytes"]
        source = a / "source"
        source.write_bytes(b"q" * 8192)
        one = allocated(source)
        self.assertEqual(campaign.check()["used_bytes"], initial + one)
        os.link(source, b / "linked")
        self.assertEqual(campaign.check()["used_bytes"], initial + 2 * one)

    def test_scandir_fails_closed_on_broken_symlink_and_unreadable_directory(self):
        bad = self.base / "bad"
        bad.mkdir()
        (bad / "broken").symlink_to(bad / "missing")
        with self.assertRaises(OSError):
            allocated(bad)
        (bad / "broken").unlink()
        unreadable = bad / "unreadable"
        unreadable.mkdir()
        os.chmod(unreadable, 0)
        try:
            with self.assertRaises(OSError):
                allocated(bad)
        finally:
            os.chmod(unreadable, 0o700)

    def test_guards_peak_drain_and_hostfree(self):
        with self.assertRaises(RuntimeError):
            self.c.check(1, snapshot=self.snapshot(LIMIT))
        with self.assertRaises(RuntimeError):
            self.c.check(100, snapshot=self.snapshot(free=256 * GIB + 1024))
        self.assertGreater(self.c.check(100, snapshot=self.snapshot())["remaining_bytes"], 0)
        with self.assertRaises(ValueError):
            Campaign.create(self.base / "other", images=[self.image, self.image])
        with self.assertRaises(ValueError):
            self.c.check(snapshot={"allocated": {}, "host_free": 100})

    def test_generator_failure_retains_failed_metadata_and_no_unowned_cleanup(self):
        from benchmarks.local_perf.dataset import generate
        keep = self.c.root / "unowned.txt"
        keep.write_text("keep")
        with patch("benchmarks.local_perf.dataset.connect", side_effect=ImportError("missing optional engine")):
            with self.assertRaises(ImportError):
                generate(self.c, "failed")
        self.assertEqual(json.loads((self.c.root / "failed/dataset.json").read_text())["status"], "FAILED")
        self.assertEqual(keep.read_text(), "keep")
        with self.assertRaises(FileExistsError):
            generate(self.c, "failed")
        with self.assertRaises(ValueError):
            self.c.cleanup("failed")


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.next = 0

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, rows, kinds=("integer",), metadata=None):
        self.next += 1
        path = self.root / (str(self.next) + ".jsonl")
        w = Writer(path, [{"name": "c" + str(i), "type": kind} for i, kind in enumerate(kinds)], metadata or {})
        try:
            for row in rows:
                w.row(row)
            w.finish()
        finally:
            w.close()
        return path

    def compare(self, a, b, **options):
        return compare(a, b, scratch=self.root, **options)

    def test_after_10k_and_duplicate_loss(self):
        a = self.result(([i] for i in range(11001)))
        b = self.result(([i] for i in reversed(range(11001))))
        self.assertTrue(self.compare(a, b))
        c = self.result(([i] for i in range(11000)))
        with self.assertRaises(ValueError):
            self.compare(a, c)
        d = self.result(([i if i != 11000 else 9999] for i in range(11001)))
        with self.assertRaises(ValueError):
            self.compare(a, d)
        x, y = self.result([[1], [1], [2]]), self.result([[1], [2], [2]])
        with self.assertRaises(ValueError):
            self.compare(x, y)

    def test_signed_zero_matches_in_ordered_and_unordered_comparison(self):
        a = self.result([[0.0], [1.5]], ("double",))
        b = self.result([[1.5], [-0.0]], ("double",))
        self.assertTrue(self.compare(a, b))
        self.assertTrue(self.compare(self.result([[0.0]], ("double",)), self.result([[-0.0]], ("double",)), ordered=True))
        with self.assertRaises(ValueError):
            self.compare(a, self.result([[1.5], [1e-300]], ("double",)))

    def test_types_decimal_exponents_dates_timestamp_precision(self):
        kinds = ("bigint", "decimal(38,8)", "date", "timestamp(6) with time zone", "varchar", "boolean")
        a = self.result([[2**62, Decimal("12345678901234567890.12340000"), dt.date(2026, 1, 1),
                          dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), "", True]], kinds)
        b = self.result([[2**62, Decimal("12345678901234567890.1234"), "2026-01-01",
                          "2026-01-01T09:00:00+09:00", "", True]], kinds)
        self.assertTrue(self.compare(a, b))
        for bad, kind in (("2026-01-01T00:00:00.1234567Z", "timestamp(6)"),
                          (Decimal("1.123"), "decimal(4,2)")):
            with self.assertRaises(ValueError):
                self.result([[bad]], (kind,))
        with self.assertRaises(ValueError):
            self.compare(self.result([[None]], ("varchar",)), self.result([[""]], ("varchar",)))

    def test_order_schema_metadata_and_float(self):
        a, b = self.result([[1], [2]]), self.result([[2], [1]])
        with self.assertRaises(ValueError):
            self.compare(a, b, ordered=True)
        with self.assertRaises(ValueError):
            self.compare(a, self.result([[1], [2]], ("bigint",)))
        with self.assertRaises(ValueError):
            self.compare(a, self.result([[1], [2]], metadata={"snapshot": "different"}))
        a = self.result([[1.0], [1.15]], ("double",))
        b = self.result([[1.09], [.91]], ("double",))
        with self.assertRaises(ValueError):
            self.compare(a, b)
        # Requires reassignment: greedy 1->1.09 would leave 1.15 unmatched.
        self.assertTrue(self.compare(a, b, abs_tol=.1))
        with self.assertRaises(ValueError):
            self.compare(a, b, abs_tol=-1)

    def test_eof_malformed_and_truncation(self):
        a = self.result([[1]])
        for content in (a.read_text().rsplit("\n", 2)[0] + "\n", a.read_text()[:-1],
                        a.read_text().replace('["i","1"]', '["i","01"]'),
                        a.read_text() + "{}\n", a.read_text().replace('"rows":1', '"rows":2')):
            b = self.root / "bad.jsonl"
            b.write_text(content)
            with self.assertRaises(ValueError):
                self.compare(a, b)


if __name__ == "__main__":
    unittest.main()
