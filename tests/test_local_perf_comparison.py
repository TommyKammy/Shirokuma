"""Full-stream comparison contracts and bounded comparison budget checks."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import results


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def stream(self, name, rows, kind="double", metadata=None):
        path = self.root / name
        writer = results.Writer(path, [results.column("value", kind)],
                                {} if metadata is None else metadata)
        try:
            for value in rows:
                writer.row([value])
            writer.finish()
        finally:
            writer.close()
        return path

    def compare(self, left, right, **kwargs):
        return results.compare(left, right, scratch=self.scratch, **kwargs)

    def rewrite(self, path, transform):
        lines = path.read_text().splitlines()
        transform(lines)
        path.write_text("\n".join(lines) + "\n")

    def test_exact_float_multiset_with_tolerance_skips_candidate_matching(self):
        values = [float(i % 47) for i in range(1974)]
        left = self.stream("left", values)
        right = self.stream("right", reversed(values))
        check = Mock()
        with patch.object(results, "equal", side_effect=AssertionError("candidate matching")):
            self.assertTrue(self.compare(left, right, abs_tol=0.01, rel_tol=0.001, check=check))
        self.assertGreaterEqual(check.call_count, 2 + 2 * (1974 // 256))
        self.assertLess(check.call_count, 1974 // 4)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_late_unordered_mismatch_is_not_hidden_by_exact_prefix(self):
        left = self.stream("left", list(range(1024)) + [9000])
        right = self.stream("right", list(range(1024)) + [9002])
        for tolerance in (0, 0.1):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                self.compare(left, right, abs_tol=tolerance)

    def test_duplicate_multiplicity_and_row_count_are_checked(self):
        left = self.stream("left", [1, 1, 2])
        for name, values in (("same_count", [1, 2, 2]), ("short", [1, 2])):
            right = self.stream(name, values)
            for tolerance in (0, 0.01):
                with self.subTest(name=name, tolerance=tolerance), self.assertRaises(ValueError):
                    self.compare(left, right, abs_tol=tolerance)

    def test_exact_partial_pair_does_not_preempt_augmenting_path(self):
        # Pairing the shared zero first strands 1; the full matching is 0 -> -1, 1 -> 0.
        left = self.stream("left", [0, 1])
        right = self.stream("right", [0, -1])
        self.assertTrue(self.compare(left, right, abs_tol=1))

    def test_ordered_checks_start_end_and_each_256_rows(self):
        for size in (0, 255, 256, 1025):
            with self.subTest(size=size):
                left = self.stream("left_" + str(size), range(size))
                right = self.stream("right_" + str(size), range(size))
                check = Mock()
                self.assertTrue(self.compare(left, right, ordered=True, check=check))
                self.assertEqual(check.call_count, 2 + size // 256)

    def test_ordered_late_mismatch_and_terminal_footer_rejected(self):
        left = self.stream("left", range(1025))
        right = self.stream("different", list(range(1024)) + [9999])
        with self.assertRaisesRegex(ValueError, "ordered result mismatch"):
            self.compare(left, right, ordered=True)
        right = self.stream("bad_footer", range(1025))
        self.rewrite(right, lambda lines: lines.__setitem__(-1, '{"terminal":"SUCCESS","rows":1025,"sha256":"bad"}'))
        with self.assertRaisesRegex(ValueError, "terminal footer"):
            self.compare(left, right, ordered=True)

    def test_nonexact_tolerant_matching_batches_start_and_candidate_checks(self):
        left = self.stream("left", range(33))
        right = self.stream("right", [i + 0.125 for i in range(33)])
        check = Mock()
        equal = results.equal
        with patch.object(results, "equal", wraps=equal) as candidates:
            self.assertTrue(self.compare(left, right, abs_tol=0.25, check=check))
        self.assertGreater(candidates.call_count, 256)
        self.assertGreaterEqual(check.call_count, 2 + candidates.call_count // 256)
        self.assertLess(check.call_count, candidates.call_count // 8)

    def test_both_exact_streams_validate_footer_even_with_tolerance(self):
        for side in ("left", "right"):
            for fault in ("missing", "digest", "trailing"):
                with self.subTest(side=side, fault=fault):
                    left = self.stream("l_" + side + fault, [1, 1, 2])
                    right = self.stream("r_" + side + fault, [2, 1, 1])
                    target = left if side == "left" else right
                    def corrupt(lines):
                        if fault == "missing":
                            lines.pop()
                        elif fault == "digest":
                            footer = json.loads(lines[-1])
                            footer["sha256"] = "bad"
                            lines[-1] = results.dumps(footer)
                        else:
                            lines.append("[]")
                    self.rewrite(target, corrupt)
                    with self.assertRaises(ValueError):
                        self.compare(left, right, abs_tol=0.1)

    def test_unknown_schema_and_nonfinite_typed_values_still_rejected(self):
        for bad_type in ("array(integer)", "unknown"):
            with self.subTest(kind=bad_type), self.assertRaisesRegex(ValueError, "unsupported result type"):
                results.column("value", bad_type)
        for raw in ("nan", "inf", "-inf"):
            with self.subTest(raw=raw):
                left = self.stream("l_" + raw, [1])
                right = self.stream("r_" + raw, [1])
                # A valid checksum cannot make an invalid typed value acceptable.
                def corrupt(lines):
                    lines[1] = results.dumps([["f", raw]])
                    footer = json.loads(lines[-1])
                    footer["sha256"] = hashlib.sha256((lines[1] + "\n").encode()).hexdigest()
                    lines[-1] = results.dumps(footer)
                self.rewrite(right, corrupt)
                with self.assertRaisesRegex(ValueError, "nonfinite floating"):
                    self.compare(left, right, abs_tol=0.1)

    def test_header_schema_and_metadata_guards(self):
        left = self.stream("left", [1])
        variants = (
            ("metadata", lambda h: h.update(metadata={"query": "different"})),
            ("name", lambda h: h["columns"][0].update(name="other")),
            ("type", lambda h: h["columns"][0].update(type="real")),
            ("unknown", lambda h: h["columns"][0].update(type="array(integer)")),
            ("format", lambda h: h.update(format=2)),
            ("policy", lambda h: h.update(timestamp_policy="local")),
        )
        for name, mutate in variants:
            with self.subTest(name=name):
                right = self.stream(name, [1])
                def corrupt(lines):
                    header = json.loads(lines[0])
                    mutate(header)
                    lines[0] = results.dumps(header)
                self.rewrite(right, corrupt)
                with self.assertRaises(ValueError):
                    self.compare(left, right, abs_tol=0.1)

    def test_invalid_tolerances_rejected(self):
        for value in (-1, float("nan"), float("inf")):
            for name in ("abs_tol", "rel_tol"):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "invalid floating tolerance"):
                    self.compare(self.root / "missing", self.root / "also_missing", **{name: value})

    def test_callback_deadline_and_capacity_interruptions_clean_up(self):
        for ordered in (False, True):
            for exception in (TimeoutError("deadline"), OSError("capacity")):
                with self.subTest(ordered=ordered, exception=exception):
                    left = self.stream("l_" + str(ordered) + type(exception).__name__, range(1025))
                    right = self.stream("r_" + str(ordered) + type(exception).__name__, range(1025))
                    calls = []
                    def check():
                        calls.append(1)
                        if len(calls) == 2:
                            raise exception
                    with self.assertRaises(type(exception)) as caught:
                        self.compare(left, right, ordered=ordered, check=check)
                    self.assertIs(caught.exception, exception)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(list(self.scratch.iterdir()), [])

    def test_start_callback_runs_before_opening_files(self):
        with patch.object(results, "Reader", side_effect=AssertionError("reader opened")):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                self.compare(self.root / "missing", self.root / "missing2",
                             check=Mock(side_effect=TimeoutError("deadline")))

    def test_final_callback_can_interrupt_small_exact_streams(self):
        left = self.stream("left", [1])
        right = self.stream("right", [1])
        for ordered in (False, True):
            with self.subTest(ordered=ordered):
                check = Mock(side_effect=[None, TimeoutError("final deadline")])
                with self.assertRaisesRegex(TimeoutError, "final deadline"):
                    self.compare(left, right, ordered=ordered, abs_tol=0.1, check=check)
                self.assertEqual(check.call_count, 2)
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_invalid_right_header_closes_left_reader(self):
        left = self.stream("left", [1])
        right = self.stream("right", [1])
        self.rewrite(right, lambda lines: lines.__setitem__(0, "{}"))
        reader = results.Reader
        opened = []
        def track(path):
            stream = reader(path)
            opened.append(stream)
            return stream
        with patch.object(results, "Reader", side_effect=track):
            with self.assertRaisesRegex(ValueError, "invalid header"):
                self.compare(left, right)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].f.closed)

    def test_sqlite_progress_exception_preserved_and_database_closed(self):
        connect = sqlite3.connect
        for error_type in (TimeoutError, OSError):
            with self.subTest(error_type=error_type):
                state = {"progress": False, "calls": 0, "connections": []}
                class Connection(sqlite3.Connection):
                    def set_progress_handler(self, callback, instructions):
                        self.instructions = instructions
                        def progress():
                            state["progress"] = True
                            state["calls"] += 1
                            try:
                                return callback()
                            finally:
                                state["progress"] = False
                        super().set_progress_handler(progress, instructions)
                    def close(self):
                        self.was_closed = True
                        super().close()
                def tracked_connect(*args, **kwargs):
                    kwargs["factory"] = Connection
                    db = connect(*args, **kwargs)
                    state["connections"].append(db)
                    return db
                failure = error_type("sqlite budget")
                def check():
                    if state["progress"]:
                        raise failure
                left = self.stream("progress_l_" + error_type.__name__, range(1974))
                right = self.stream("progress_r_" + error_type.__name__, range(1974))
                with patch.object(results.sqlite3, "connect", side_effect=tracked_connect):
                    with self.assertRaises(error_type) as caught:
                        self.compare(left, right, abs_tol=0.1, check=check)
                self.assertIs(caught.exception, failure)
                self.assertGreater(state["calls"], 0)
                self.assertEqual(len(state["connections"]), 1)
                db = state["connections"][0]
                self.assertEqual(db.instructions, 10000)
                self.assertTrue(db.was_closed)
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute("SELECT 1")
                self.assertEqual(list(self.scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
