"""Fixed-input arithmetic, cross-engine types, snapshot scope and bounded streams."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_benchmark import workload as W


TOKEN = 'a' * 32
SNAPSHOT = 123456789


def row(key=1, line=1, *, quantity='2.00', price='100.10', discount='0.05',
        tax='0.02', day='1994-01-01', flag='A', status='F'):
    return [key, line, flag, status, quantity, price, discount, tax, day]


def oracle(rows):
    result = W.DatasetOracle()
    for value in rows:
        result.add(value)
    return result.finish()


def normalized(case, values, engine='trino'):
    schema = W.result_schema(case, engine)
    return W.normalize_result(case, engine, [x['name'] for x in schema], [x['type'] for x in schema], values)


def fraction_string(value, scale=6):
    scaled = value * 10**scale
    assert scaled.denominator == 1
    integer = scaled.numerator
    return f'{integer // 10**scale}.{integer % 10**scale:0{scale}d}'


class FixedSqlTests(unittest.TestCase):
    def test_namespace_and_snapshot_are_fixed_for_both_engines(self):
        for engine in W.ENGINES:
            for case in W.CASES:
                sql = W.query_sql(engine, case, TOKEN, SNAPSHOT)
                self.assertTrue(sql.startswith('SELECT '))
                self.assertIn('spark_' + TOKEN, sql)
                self.assertIn(('FOR VERSION AS OF ' if engine == 'trino' else 'VERSION AS OF ') + str(SNAPSHOT), sql)
                self.assertNotIn('tpch.', sql)
                self.assertNotIn(';', sql)
        self.assertEqual(W.table_name(TOKEN), 'iceberg.local_experiment.spark_' + TOKEN)
        self.assertEqual(W.table_location(TOKEN), 's3://shirokuma-lakehouse/l1/local_experiment/spark_' + TOKEN)
        self.assertEqual(W.marker(TOKEN), 'shirokuma-local-spark-benchmark:' + TOKEN)
        self.assertEqual(W.EXPECTED_MEASUREMENTS, 16)

    def test_invalid_identity_cannot_become_sql(self):
        for bad in (None, True, 7, 'a'*31, 'A'*32, TOKEN+'; DROP TABLE other'):
            with self.subTest(token=bad), self.assertRaises(W.WorkloadError):
                W.query_sql('trino', 'q6', bad, SNAPSHOT)
        for bad in (True, 0, -1, 2**63, str(SNAPSHOT), None, 1.5):
            with self.subTest(snapshot=bad), self.assertRaises(W.WorkloadError):
                W.table_rows_sql(TOKEN, bad)
        for engine, case in [('mysql', 'q6'), ('trino', 'q1'), ('spark', 'SELECT secret'), ([], 'q6')]:
            with self.subTest(engine=engine, case=case), self.assertRaises(W.WorkloadError):
                W.query_sql(engine, case, TOKEN, SNAPSHOT)

    def test_source_projection_and_raw_validation_do_not_change_catalog(self):
        selected = W.source_select_sql()
        self.assertIn('FROM tpch."sf0.1".lineitem', selected)
        self.assertNotIn('ORDER BY', selected)
        self.assertEqual(W.source_rows_sql(), selected + ' ORDER BY orderkey, linenumber')
        self.assertEqual(len(W.source_schema()), 9)
        self.assertEqual(W.RAW_SOURCE_COLUMN_TYPES[4:8], ('double',) * 4)
        validation = W.source_validation_sql()
        for name in ('quantity', 'extendedprice', 'discount', 'tax'):
            self.assertIn('NOT is_finite(' + name + ')', validation)
            self.assertIn('AS DOUBLE) IS DISTINCT FROM ' + name, validation)
            self.assertIn(name + ' IS NULL', validation)
        for name in W.VALIDATION_COLUMNS:
            self.assertIn(' AS ' + name, validation)
        self.assertNotIn('SET ', validation)

    def test_dates_are_fixed_and_decimal_averages_are_not_silently_compared(self):
        q1 = W.query_sql('trino', 'q1_sums', TOKEN, SNAPSHOT)
        q6 = W.query_sql('spark', 'q6', TOKEN, SNAPSHOT)
        cutoff = date(1998, 12, 1) - timedelta(days=90)
        self.assertIn("DATE '" + cutoff.isoformat() + "'", q1)
        self.assertNotIn('avg(', q1.lower())
        self.assertEqual(q1.count('DECIMAL(28,6)'), 4)
        self.assertEqual(q6.count('DECIMAL(28,6)'), 1)
        self.assertIn("shipdate < DATE '1995-01-01'", q6)
        self.assertIn("BETWEEN CAST('0.05' AS DECIMAL(3,2)) AND CAST('0.07' AS DECIMAL(3,2))", q6)

    def test_decimal_constants_use_cast_supported_by_both_engines(self):
        # Spark 4.1.3 AstBuilder.visitTypeConstructor supports date/time/binary,
        # not Trino's DECIMAL 'value' constructor. Exact string CAST is portable.
        for engine in W.ENGINES:
            q1 = W.query_sql(engine, 'q1_sums', TOKEN, SNAPSHOT)
            q6 = W.query_sql(engine, 'q6', TOKEN, SNAPSHOT)
            self.assertNotIn("DECIMAL '", q1 + q6)
            self.assertEqual(q1.count("CAST('1.00' AS DECIMAL(3,2))"), 3)
            for value, kind in [('0.05', '3,2'), ('0.07', '3,2'), ('24.00', '4,2')]:
                self.assertIn("CAST('" + value + "' AS DECIMAL(" + kind + '))', q6)

    def test_schema_return_values_do_not_mutate_the_contract(self):
        schema = W.source_schema('spark')
        self.assertEqual([r['type'] for r in schema[:4]], ['bigint', 'int', 'string', 'string'])
        schema[0]['name'] = 'secret'
        self.assertEqual(W.source_schema()[0]['name'], 'orderkey')

    def test_exact_source_can_be_embedded_without_repository_imports(self):
        source = Path(W.__file__).read_text()
        sandbox = {'__name__': 'fixed_workload_inline'}
        exec(compile(source, '<fixed-workload>', 'exec'), sandbox)
        self.assertEqual(sandbox['query_sql']('spark', 'q6', TOKEN, SNAPSHOT),
                         W.query_sql('spark', 'q6', TOKEN, SNAPSHOT))
        result = sandbox['DatasetOracle']()
        result.add(row())
        self.assertEqual(result.finish(), oracle([row()]))


class DecimalOracleTests(unittest.TestCase):
    def test_exact_group_sums_discount_tax_and_cutoffs(self):
        data = [row(), row(1, 2, quantity='23.00', price='200.20', discount='0.07', tax='0.08', day='1994-12-31'),
                row(2, quantity='24.00', price='5.00', discount='0.06', tax='0.00', day='1995-01-01', flag='N', status='O'),
                row(3, quantity='1.00', price='1.00', discount='0.00', tax='0.00', day='1998-09-02', flag='N', status='O'),
                row(4, quantity='1.00', price='50.00', discount='0.10', tax='0.08', day='1998-09-03', flag='R')]
        result = oracle(data)
        self.assertEqual(result['row_count'], 5)
        self.assertEqual(result['expected']['q1_sums']['rows'], [
            ['A', 'F', '25.000000', '300.300000', '281.281000', '298.077780', 2],
            ['N', 'O', '25.000000', '6.000000', '5.700000', '5.700000', 2]])
        self.assertEqual(result['expected']['q6']['rows'], [['19.019000']])

    def test_q6_inclusive_discount_and_exclusive_date_quantity_boundaries(self):
        cases = [('1994-01-01', '0.05', '23.00', True), ('1994-12-31', '0.07', '23.00', True),
                 ('1993-12-31', '0.06', '23.00', False), ('1995-01-01', '0.06', '23.00', False),
                 ('1994-06-01', '0.04', '23.00', False), ('1994-06-01', '0.08', '23.00', False),
                 ('1994-06-01', '0.06', '24.00', False)]
        for day, discount, quantity, included in cases:
            with self.subTest(day=day, discount=discount, quantity=quantity):
                output = oracle([row(day=day, discount=discount, quantity=quantity, price='100.00')])
                wanted = format(Decimal(discount)*100, '.6f') if included else None
                self.assertEqual(output['expected']['q6']['rows'], [[wanted]])

    def test_fraction_reference_across_all_six_groups(self):
        data, groups, revenue = [], {}, Fraction(0)
        matched = False
        days = ['1994-01-01', '1994-12-31', '1998-09-02', '1998-09-03', '1995-01-01', '1993-12-31']
        for i in range(120):
            q, p, d, t = Fraction(i % 50 + 1), Fraction(i*123+1, 100), Fraction(i % 11, 100), Fraction(i % 9, 100)
            flag, status, day = ('A', 'N', 'R')[i % 3], ('F', 'O')[(i // 3) % 2], days[i % 6]
            data.append(row(i+1, quantity=fraction_string(q, 2), price=fraction_string(p, 2),
                            discount=fraction_string(d, 2), tax=fraction_string(t, 2), day=day, flag=flag, status=status))
            if day <= '1998-09-02':
                acc = groups.setdefault((flag, status), [Fraction(0)]*4 + [0])
                for j, value in enumerate([q, p, p*(1-d), p*(1-d)*(1+t)]):
                    acc[j] += value
                acc[4] += 1
            if '1994-01-01' <= day < '1995-01-01' and Fraction(5,100) <= d <= Fraction(7,100) and q < 24:
                matched = True
                revenue += p*d
        actual = oracle(data)['expected']
        wanted = [[*key, *[fraction_string(v) for v in values[:4]], values[4]] for key, values in sorted(groups.items())]
        self.assertEqual(actual['q1_sums']['rows'], wanted)
        self.assertEqual(actual['q6']['rows'], [[fraction_string(revenue) if matched else None]])

    def test_ambient_decimal_precision_cannot_round_the_oracle(self):
        data = [row(), row(2, price='9999999999999.99', discount='0.07', tax='0.08')]
        expected = oracle(data)
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(oracle(data), expected)

    def test_empty_sql_aggregate_is_null_not_zero(self):
        result = oracle([])
        self.assertEqual(result['row_count'], 0)
        self.assertEqual(result['expected']['q1_sums']['rows'], [])
        self.assertEqual(result['expected']['q6']['rows'], [[None]])
        self.assertNotEqual(result['expected']['q6']['sha256'], normalized('q6', [['0.000000']])['sha256'])

    def test_source_hash_is_independent_of_decimal_representation_and_date_object(self):
        original = row()
        equivalent = row(quantity=Decimal('2'), price=Decimal('100.100'), discount=Decimal('.05'),
                         tax=Decimal('.020'), day=date(1994, 1, 1))
        self.assertEqual(oracle([original]), oracle([equivalent]))
        # Changing an excluded row cannot hide behind identical query outputs.
        first, changed = oracle([row(day='1998-09-03')]), oracle([row(day='1998-09-03', tax='0.03')])
        self.assertEqual(first['expected'], changed['expected'])
        self.assertNotEqual(first['logical_sha256'], changed['logical_sha256'])

    def test_hash_binds_the_full_ordered_input_with_a_schema_header(self):
        schema = [{'name': n, 'type': t} for n, t in zip(
            ['orderkey','linenumber','returnflag','linestatus','quantity','extendedprice','discount','tax','shipdate'],
            ['bigint','int','string','string','decimal(12,2)','decimal(15,2)','decimal(3,2)','decimal(3,2)','date'])]
        canonical = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        content = canonical({'workload_id': 'tpch-sf0.1-lineitem-decimal-v1', 'schema': schema}) + b'\n' + canonical(row()) + b'\n'
        self.assertEqual(oracle([row()])['logical_sha256'], hashlib.sha256(content).hexdigest())

    def test_reordered_or_duplicate_keys_poison_partial_oracle(self):
        for last in (row(), row(1, 1)):
            accumulator = W.DatasetOracle()
            accumulator.add(row(1, 2))
            with self.assertRaises(W.WorkloadError):
                accumulator.add(last)
            with self.assertRaisesRegex(W.WorkloadError, '^ORACLE_FAILED$'):
                accumulator.finish()
        self.assertEqual(oracle([row(), row(1,2)])['row_count'], 2)

    def test_invalid_source_values_are_not_coerced_or_leaked(self):
        changes = [(0, True), (1, 0), (1, 8), (2, 'SECRET'), (3, None), (4, 2.0),
                   (4, '2.01'), (5, '0.00'), (5, None), (5, '100.001'), (5, '1e2'),
                   (6, '0.11'), (7, '0.09'), (8, '1994-02-30'), (8, '1994-1-01'),
                   (8, '1994-01-01T00:00:00Z'), (8, datetime(1994,1,1)), (8, '2000-01-01')]
        for index, value in changes:
            with self.subTest(index=index, value=value):
                bad = row(); bad[index] = value
                accumulator = W.DatasetOracle()
                with self.assertRaises(W.WorkloadError) as caught:
                    accumulator.add(bad)
                self.assertNotIn('SECRET', str(caught.exception))
                with self.assertRaises(W.WorkloadError):
                    accumulator.finish()

    def test_row_and_byte_bounds_cannot_be_ignored_to_finish_a_partial_stream(self):
        with patch.object(W, 'MAX_SOURCE_ROWS', 1):
            accumulator = W.DatasetOracle()
            accumulator.add(row())
            with self.assertRaisesRegex(W.WorkloadError, '^SOURCE_ROW_LIMIT$'):
                accumulator.add(row(2))
            with self.assertRaisesRegex(W.WorkloadError, '^ORACLE_FAILED$'):
                accumulator.finish()
        with patch.object(W, 'MAX_SOURCE_BYTES', 1):
            accumulator = W.DatasetOracle()
            with self.assertRaisesRegex(W.WorkloadError, '^SOURCE_BYTE_LIMIT$'):
                accumulator.add(row())
            with self.assertRaises(W.WorkloadError):
                accumulator.finish()

    def test_finished_receipt_is_detached_and_accumulator_is_closed(self):
        accumulator = W.DatasetOracle()
        accumulator.add(row())
        receipt = accumulator.finish()
        expected = deepcopy(receipt)
        receipt['expected']['q6']['rows'][0][0] = '999999'
        self.assertEqual(accumulator.finish(), expected)
        with self.assertRaisesRegex(W.WorkloadError, '^ORACLE_CLOSED$'):
            accumulator.add(row(2))


class ResultNormalizationTests(unittest.TestCase):
    def test_observed_trino_decimal_comma_space_is_spelling_only(self):
        result = W.normalize_result('q6', 'trino', ['revenue'], ['decimal(28, 6)'], [['19.019000']])
        self.assertEqual(result, normalized('q6', [['19.019000']], 'spark'))
        self.assertEqual(W.normalize_column_types('trino', ['decimal(12, 2)', 'varchar(1)', 'date']),
                         ['decimal(12,2)', 'varchar(1)', 'date'])
        for bad in ('decimal(28, 5)', 'decimal(27, 6)', 'decimal(28,  6)', 'DECIMAL(28, 6)', 'decimal(28,\t6)'):
            with self.subTest(kind=bad), self.assertRaises(W.WorkloadError):
                W.normalize_result('q6', 'trino', ['revenue'], [bad], [['0.000000']])
        with self.assertRaises(W.WorkloadError):
            W.normalize_result('q6', 'spark', ['revenue'], ['decimal(28, 6)'], [['0.000000']])

    def test_trino_decimal_strings_and_spark_decimals_have_identical_result_hashes(self):
        expected = oracle([row(), row(2, flag='R')])['expected']['q1_sums']
        spark_rows = [[*r[:2], *[Decimal(v) for v in r[2:-1]], r[-1]] for r in reversed(expected['rows'])]
        self.assertEqual(normalized('q1_sums', spark_rows, 'spark'), expected)
        self.assertEqual(normalized('q6', [['-0.000000']]), normalized('q6', [[Decimal('0')]], 'spark'))

    def test_decimal_float_nan_overflow_or_extra_precision_is_rejected(self):
        for value in (1.0, 1, True, 'NaN', 'Infinity', '1e1', '+1.00', '00.00', '0.0000001',
                      '1' + '0'*22, Decimal('NaN'), Decimal('Infinity'), Decimal('1E+1000000'), '0'*1000):
            with self.subTest(value=str(value)[:40]), self.assertRaises(W.WorkloadError):
                normalized('q6', [[value]])

    def test_null_is_preserved_and_unknown_type_is_not_coerced(self):
        self.assertEqual(normalized('q6', [[None]], 'spark')['rows'], [[None]])
        with self.assertRaisesRegex(W.WorkloadError, '^RESULT_TYPES_INVALID$'):
            W.normalize_result('q6', 'trino', ['revenue'], ['double'], [['0.000000']])
        with self.assertRaisesRegex(W.WorkloadError, '^RESULT_COLUMNS_INVALID$'):
            W.normalize_result('q6', 'trino', ['unknown'], ['decimal(28,6)'], [['0.000000']])

    def test_wrong_row_shapes_duplicate_groups_and_boolean_counts_are_rejected(self):
        valid = ['A', 'F', '1.000000', '1.000000', '1.000000', '1.000000', 1]
        for rows in ([valid, valid], [[*valid[:-1], True]], [[*valid[:-1], 0]], [valid[:-1]], [valid]*7):
            with self.subTest(rows=rows), self.assertRaises(W.WorkloadError):
                normalized('q1_sums', rows)
        for rows in ([], [[None], [None]], [[None, None]], 'not rows'):
            with self.subTest(rows=rows), self.assertRaises(W.WorkloadError):
                normalized('q6', rows)

    def test_result_hash_independently_covers_schema_and_values(self):
        actual = normalized('q6', [['19.019']])
        material = {'schema': [{'name': 'revenue', 'type': 'decimal(28,6)'}], 'rows': [['19.019000']]}
        encoded = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(actual['sha256'], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(actual['row_count'], 1)


if __name__ == '__main__':
    unittest.main()
