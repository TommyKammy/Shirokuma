"""Projected ORDER BY contracts and streamed validation, without SQL engines."""
from decimal import Decimal, localcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.local_perf.results import (
    Writer, cell, column, order_values, validate_order, validate_order_contract,
)


def subtract(left=0, right=1, descending=False, nulls="last"):
    return dict(expression=dict(op="subtract", left=left, right=right),
                descending=descending, nulls=nulls)


def case(condition=0, value=0, then=1, descending=False, nulls="last"):
    return dict(expression=dict(op="case_equal", condition=condition, value=value, then=then),
                descending=descending, nulls=nulls)


class ProjectedOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "result.jsonl"

    def schema(self, *kinds):
        return [column("c" + str(i), kind) for i, kind in enumerate(kinds)]

    def stream(self, columns, rows):
        self.path.unlink(missing_ok=True)
        writer = Writer(self.path, columns, {})
        try:
            for row in rows:
                writer.row(row)
            writer.finish()
        finally:
            writer.close()
        return self.path

    def values(self, columns, row, keys):
        validate_order_contract(columns, keys)
        typed = [cell(v, c["type"]) for v, c in zip(row, columns)]
        return order_values(typed, columns, keys)

    def test_decimal_38_and_mixed_integer_exact_with_small_ambient_precision(self):
        columns = self.schema("decimal(38,0)", "decimal(38,0)", "bigint")
        large = "99999999999999999999999999999999999999"
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(self.values(columns, [large, "1", 1], [subtract(), subtract(0, 2)]),
                             [Decimal("99999999999999999999999999999999999998")] * 2)
            self.assertEqual(context.prec, 6)
            # These adjacent keys collapse if subtraction uses default precision.
            self.stream(columns, [[large, "1", 0], [large, "2", 0]])
            with self.assertRaisesRegex(ValueError, "violates ORDER BY"):
                validate_order(self.path, [subtract()])
        columns = self.schema("hugeint", "decimal(38,38)")
        self.assertEqual(self.values(columns, [10**38, "0.00000000000000000000000000000000000001"],
                                     [subtract()]),
                         [Decimal("99999999999999999999999999999999999999.99999999999999999999999999999999999999")])
        self.assertEqual(self.values(columns, [0, "0.00000000000000000000000000000000000001"],
                                     [subtract(1, 0)]), [Decimal("1e-38")])

    def test_double_coercion_and_integer_subtraction(self):
        columns = self.schema("decimal(38,0)", "double", "bigint", "integer")
        self.assertEqual(self.values(columns, ["9007199254740993", 9007199254740992.0, 9, 4],
                                     [subtract(), subtract(2, 3)]), [0.0, 5])
        columns = self.schema("real", "bigint")
        self.assertEqual(self.values(columns, [2.5, 1], [subtract()]), [1.5])

    def test_nulls_first_last_both_directions_for_both_expressions(self):
        columns = self.schema("integer", "integer")
        for factory in (subtract, case):
            for descending in (False, True):
                for nulls in ("first", "last"):
                    with self.subTest(expression=factory.__name__, descending=descending, nulls=nulls):
                        key = factory(descending=descending, nulls=nulls)
                        rows = [[0, -1], [0, -2]] if factory is subtract else [[0, 1], [0, 2]]
                        if descending:
                            rows.reverse()
                        null_rows = [[None, 1], [1, None]] if factory is subtract else [[None, 1], [1, 99], [0, None]]
                        rows = null_rows + rows if nulls == "first" else rows + null_rows
                        validate_order(self.stream(columns, rows), [key])
                        self.assertEqual(self.values(columns, null_rows[0], [key]), [None])
                        validate_order_contract(columns, [key])
                        with self.assertRaisesRegex(ValueError, "violates ORDER BY"):
                            validate_order(self.stream(columns, list(reversed(rows))), [key])

    def test_case_nonzero_literal_and_projected_then_type(self):
        columns = self.schema("bigint", "varchar")
        keys = [case(value=7)]
        for row, expected in (([7, "a"], "a"), ([0, "b"], None), ([None, "c"], None)):
            self.assertEqual(self.values(columns, row, keys), [expected])

    def test_derived_ties_use_ordinal_tiebreaker_and_detect_late_violation(self):
        columns = self.schema("integer", "integer", "varchar")
        tie = dict(index=2, descending=False, nulls="last")
        for key, pair in ((subtract(), [[2, 1], [3, 2]]), (case(), [[0, 1], [0, 1]])):
            with self.subTest(key=key):
                validate_order(self.stream(columns, [pair[0] + ["a"], pair[1] + ["b"]]), [key, tie])
                rows = [pair[0] + ["a"]] * 300 + [pair[1] + [""]]
                with self.assertRaisesRegex(ValueError, "violates ORDER BY"):
                    validate_order(self.stream(columns, rows), [key, tie])

    def test_invalid_contracts_rejected_without_eval(self):
        columns = self.schema("integer", "decimal(38,2)", "varchar", "boolean", "double", "date")
        ordinal = dict(index=0, descending=False, nulls="last")
        bad = [None, {}, ["ORDER BY 1"],
               [dict(ordinal, index=True)], [dict(ordinal, index=-1)], [dict(ordinal, index=6)],
               [dict(ordinal, descending=1)], [dict(ordinal, nulls="auto")],
               [dict(ordinal, sql="__import__('os').system('false')")],
               [dict(subtract(), index=0)],
               [dict(expression="__import__('os')", descending=False, nulls="last")],
               [subtract(left={"op": "subtract", "left": 0, "right": 1})],
               [subtract(left=True)], [subtract(right="1")], [subtract(right=6)],
               [subtract(right=2)], [subtract(right=3)], [subtract(right=5)],
               [case(condition=1)], [case(condition=3)], [case(condition=4)],
               [case(value=True)], [case(value=0.0)], [case(value="0")],
               [case(then=False)], [case(then=6)],
               [dict(subtract(), expression=dict(op="eval", left=0, right=1))],
               [dict(subtract(), expression=dict(op="subtract", left=0, right=1, sql="1"))],
               [dict(case(), expression=dict(op="case_equal", condition=0, value=0, then=1, otherwise=2))],
               [ordinal, dict(ordinal, descending=True)],
               [subtract(), subtract(descending=True, nulls="first")],
               [case(), case(nulls="first")]]
        with patch("builtins.eval", side_effect=AssertionError("eval called")):
            for keys in bad:
                with self.subTest(keys=keys), self.assertRaises(ValueError):
                    validate_order_contract(columns, keys)
            self.assertEqual(self.values(columns, [0, "1.25", "a", True, 2.5, "2024-01-01"],
                                         [subtract(), case()]), [Decimal("-1.25"), Decimal("1.25")])
        with self.assertRaisesRegex(ValueError, "unsupported result type"):
            validate_order_contract([dict(name="bad", type="array(integer)")], [ordinal])

    def test_empty_order_still_checks_all_rows_footer_and_callbacks(self):
        columns = self.schema("integer", "integer")
        for keys in ([], [subtract()], [case()]):
            for size in (0, 255, 256, 513):
                with self.subTest(keys=keys, size=size):
                    calls = []
                    validate_order(self.stream(columns, [[0, 0]] * size), keys, lambda: calls.append(1))
                    self.assertEqual(len(calls), 2 + size // 256)
            original = self.stream(columns, [[0, 0]] * 257).read_text()
            for corrupted in ("\n".join(original.splitlines()[:-1]) + "\n",
                              original.replace('"rows":257', '"rows":258'),
                              original + "[]\n",
                              original.replace('[["i","0"],["i","0"]]', '[["i","bad"],["i","0"]]')):
                self.path.write_text(corrupted)
                with self.subTest(keys=keys), self.assertRaises(ValueError):
                    validate_order(self.path, keys)

    def test_callbacks_interrupt_before_reader_and_at_batch(self):
        def stop():
            raise TimeoutError("budget")
        with patch("benchmarks.local_perf.results.Reader", side_effect=AssertionError("reader opened")):
            with self.assertRaisesRegex(TimeoutError, "budget"):
                validate_order(self.path, [], stop)
        calls = []

        def batch():
            calls.append(1)
            if len(calls) == 2:
                stop()

        with self.assertRaisesRegex(TimeoutError, "budget"):
            validate_order(self.stream(self.schema("integer"), [[0]] * 513), [], batch)
        self.assertEqual(len(calls), 2)

    def test_existing_ordinal_contract_and_decoding(self):
        columns = self.schema("varchar", "decimal(38,2)", "boolean")
        keys = [dict(index=i, descending=False, nulls="last") for i in range(3)]
        self.assertEqual(self.values(columns, ["a", "1.25", False], keys), ["a", Decimal("1.25"), False])
        validate_order(self.stream(columns, [["a", "1.25", False], ["a", "1.25", True], [None, None, None]]), keys)
        with self.assertRaisesRegex(ValueError, "violates ORDER BY"):
            validate_order(self.stream(columns, [["b", "1", True], ["a", "2", False]]), keys)


if __name__ == "__main__":
    unittest.main()
