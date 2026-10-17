"""Business benchmark contracts and evaluation without a resident Trino lab."""
from contextlib import redirect_stdout, nullcontext
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import ai_benchmark as benchmark


def result(rows, types=('decimal(20,8)',)):
    return dict(columns=[dict(name=f'col{i}', type=kind) for i, kind in enumerate(types)],
                rows=rows, truncated=False)


class BenchmarkTests(unittest.TestCase):
    def test_all_question_definitions(self):
        self.assertEqual(len(benchmark.BENCHMARKS), 15)
        self.assertEqual(len({entry['id'] for entry in benchmark.BENCHMARKS}), 15)
        clauses = ["status = 'COMPLETED'", 'LIMIT 5', "date_trunc('month', order_date)",
                   'AVG(total_amount)', "('CANCELLED', 'REFUNDED')", 'order_priority',
                   'GROUP BY nation', 'HAVING SUM(total_amount) > 1000', 'COUNT(DISTINCT customer_id)',
                   'SUM(i.quantity)', 'i.quantity * i.extended_price * (1 - i.discount)',
                   'GROUP BY order_date', "DECIMAL '0.1'", 'LEFT JOIN orders', 'year(order_date)']
        for entry, clause in zip(benchmark.BENCHMARKS, clauses):
            with self.subTest(id=entry['id']):
                self.assertTrue(entry['description'])
                self.assertTrue(entry['question']['en'])
                self.assertRegex(entry['question']['ja'], r'[ぁ-んァ-ヶ一-龯]')
                self.assertIn(clause, entry['gold_sql'])
                self.assertEqual(benchmark.bears.wr.check_sql_policy(entry['gold_sql']), entry['gold_sql'])

    def test_gold_business_results_on_varied_rows(self):
        """Exercise joins/status/date grains, translating only Trino date syntax.

        SQLite is a small relational oracle here, not a Trino syntax check.
        Multiple items, repeated customer names, excluded statuses, zero-order
        customers and a half-open date boundary expose common semantic mistakes.
        """
        with sqlite3.connect(':memory:') as db:
            db.create_function('date_trunc', 2, lambda grain, date: date[:7] + '-01')
            db.create_function('year', 1, lambda date: int(date[:4]))
            db.executescript("""
                CREATE TABLE fct_orders(orderkey, custkey, orderdate, totalprice, orderstatus);
                INSERT INTO fct_orders VALUES
                  (1, 1, '1994-01-01', 1200, 'F'), (2, 1, '1994-01-02', 800, 'F'),
                  (3, 2, '1994-02-01', 300, 'CANCELLED'), (4, 2, '1994-02-01', 100, 'REFUNDED'),
                  (5, 2, '1995-01-01', 1000, 'F'), (6, 3, '1994-02-02', 500, 'O'),
                  (7, 3, '1993-12-31', 50, 'F');
                CREATE TABLE dim_customers(custkey, customer_name, nation_name);
                INSERT INTO dim_customers VALUES (1, 'Same', 'JP'), (2, 'Same', 'US'),
                  (3, 'Other', 'JP'), (4, 'No orders', 'US');
                CREATE TABLE lineitem(orderkey, quantity, extendedprice, discount);
                INSERT INTO lineitem VALUES (1, 2, 100, 0.1), (1, 3, 50, 0.2),
                  (2, 1, 80, 0), (3, 4, 10, 0), (4, 1, 10, 0),
                  (5, 2, 100, 0), (6, 1, 20, 0), (7, 1, 50, 0);
                CREATE TABLE source_priorities(orderkey, orderpriority);
                INSERT INTO source_priorities VALUES (1, '1'), (2, '2'), (3, '1'), (4, '1'),
                  (5, '2'), (6, '2'), (7, '1');
            """)
            expected = [
                [(3050,)], [(1, 'Same', 2000), (2, 'Same', 1000), (3, 'Other', 50)],
                [('1993-12-01', 50), ('1994-01-01', 2000), ('1995-01-01', 1000)],
                [(762.5,)], [(400,)],
                [('1', 'CANCELLED', 1, 300), ('1', 'COMPLETED', 2, 1250),
                 ('1', 'REFUNDED', 1, 100), ('2', 'COMPLETED', 2, 1800), ('2', 'OPEN', 1, 500)],
                [('JP', 2), ('US', 2)], [(1, 2000)], [(1,)], [(1, 6), (2, 7), (3, 2)],
                [(630,)], [('1993-12-31', 1), ('1994-01-01', 1), ('1994-01-02', 1),
                           ('1994-02-01', 2), ('1994-02-02', 1), ('1995-01-01', 1)],
                [(305,)], [(0, 1), (2, 2), (3, 1)], [(1993, 50), (1994, 2000), (1995, 1000)],
            ]
            for entry, rows in zip(benchmark.BENCHMARKS, expected):
                sql = entry['gold_sql']
                for source, target in [('iceberg.marts.fct_orders', 'fct_orders'),
                                       ('iceberg.marts.dim_customers', 'dim_customers'),
                                       ('iceberg.tpch_tiny.lineitem', 'lineitem'),
                                       ('iceberg.tpch_tiny.orders', 'source_priorities')]:
                    sql = sql.replace(source, target)
                sql = re.sub(r"\b(?:DATE|DECIMAL) ('[^']*')", r'\1', sql)
                with self.subTest(id=entry['id']):
                    self.assertEqual(db.execute(sql).fetchall(), rows)

    @patch.object(benchmark.bears, 'sql')
    def test_success_discrepancy_and_syntax_failure(self, query):
        def execute(url, sql, **kwargs):
            self.assertEqual(url, 'http://trino')
            self.assertGreater(kwargs['max_rows'], 1000)
            if sql == 'SELECT broken':
                raise RuntimeError('syntax error')
            return result([[999 if sql == 'SELECT wrong' else 42]])
        query.side_effect = execute
        generated = iter(['SELECT broken', 'SELECT wrong'] + ['SELECT correct'] * 13)
        report = benchmark.run_benchmark('http://trino', lambda question: next(generated))
        self.assertEqual((report['total_questions'], report['syntax_passed'], report['semantic_passed']), (15, 14, 13))
        self.assertAlmostEqual(report['syntax_success_rate'], 14 / 15)
        self.assertAlmostEqual(report['semantic_accuracy_rate'], 13 / 15)
        self.assertEqual(query.call_count, 30)
        self.assertIn('syntax error', report['details'][0]['error'])
        self.assertTrue(report['details'][1]['syntax_valid'])
        self.assertFalse(report['details'][1]['semantic_correct'])

    @patch.object(benchmark.bears, 'sql', return_value=result([[42]]))
    def test_real_generation_path_and_resident_tunnel(self, query):
        with patch.object(benchmark.bears.wr, 'trino', return_value=nullcontext('resident')) as tunnel, \
                patch.object(benchmark.bears, 'generate_data_sql', return_value='SELECT 42') as generate:
            report = benchmark.run_benchmark()
        tunnel.assert_called_once_with()
        self.assertEqual(generate.call_count, 15)
        self.assertIn(benchmark.SOURCE_SQL, generate.call_args.args[1])
        self.assertEqual(report['semantic_passed'], 15)

    @patch.object(benchmark.bears, 'sql')
    def test_gold_failure_keeps_generated_syntax_success(self, query):
        query.side_effect = lambda url, sql, **kw: result([[1]]) if sql == 'SELECT 1' else (_ for _ in ()).throw(RuntimeError('missing gold table'))
        report = benchmark.run_benchmark('endpoint', lambda question: 'SELECT 1')
        self.assertEqual(report['syntax_passed'], 15)
        self.assertEqual(report['semantic_passed'], 0)
        self.assertIn('missing gold table', report['details'][0]['gold_error'])

    @patch.object(benchmark.bears, 'sql', return_value=result([[1]]))
    def test_generation_and_policy_failures_still_evaluate_gold(self, query):
        for generator in (lambda question: 'DROP TABLE iceberg.marts.fct_orders',
                          lambda question: (_ for _ in ()).throw(RuntimeError('model offline'))):
            with self.subTest(generator=generator):
                report = benchmark.run_benchmark('endpoint', generator)
                self.assertEqual(report['syntax_passed'], 0)
                self.assertEqual(report['semantic_passed'], 0)
        self.assertEqual(query.call_count, 30)

    def test_result_comparison(self):
        self.assertTrue(benchmark.results_match(result([[Decimal('12.1234561')], [None], [2]]),
                                                result([[2.0], ['12.1234562'], [None]])))
        for actual, gold in [
            (result([[1]]), result([[2]])), (result([[1], [1]]), result([[1]])),
            (result([[1], [1], [2]]), result([[1], [2], [2]])),
            (result([[None]]), result([[0]])),
            (result([['1']], ('varchar',)), result([[1]])),
            (result([[1, 2]], ('bigint', 'bigint')), result([[2, 1]], ('bigint', 'bigint'))),
            (result([]), result([], ('bigint', 'bigint'))),
            ({**result([[1]]), 'truncated': True}, result([[1]])),
            (result([[1.00001]]), result([[1]])),
        ]:
            with self.subTest(actual=actual, gold=gold):
                self.assertFalse(benchmark.results_match(actual, gold))
        self.assertTrue(benchmark.results_match(result([]), result([])))

    def test_cli_json(self):
        with patch.object(benchmark, 'run_benchmark', return_value={'total_questions': 15}) as run, redirect_stdout(io.StringIO()) as output:
            benchmark.main(['run', '--url', 'http://trino'])
        run.assert_called_once_with(url='http://trino')
        self.assertEqual(json.loads(output.getvalue()), {'total_questions': 15})


if __name__ == '__main__':
    unittest.main()
