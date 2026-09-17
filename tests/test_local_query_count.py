"""Pure count contract tests; no transport, SQL execution, SDK or model calls."""
from copy import deepcopy
from decimal import Decimal
import unittest

from tools.local_experiment.query_count import contract as C
from tools.local_experiment.query_filter import contract as F
from tools.local_experiment.query_snapshots import contract as S
from tools.local_experiment.query_read import contract as R
from test_query_snapshots_contract import metadata, schema, inventory, OLD, HEAD


def request(*, snapshot_id=None, column=None, literal=None):
    return C.normalize_request({'command': 'count', 'table': 'smoke.fixture_v1',
        'snapshot_id': snapshot_id, 'where': None if column is None else {'column': column, 'literal': literal}})


def window(value=None, raw=None):
    return C.project_window(raw or metadata(), value or request(), schema())


def result(count=2):
    return {'query_id': '20260918_010203_00001_abcde', 'columns': ['row_count'],
            'column_types': ['bigint'], 'rows': [[count]]}


def public(value=None, count=2):
    value = value or request()
    return C.project_count(result(count), value, schema(), window(value))


class CountContractTests(unittest.TestCase):
    def test_exact_nullable_request_and_describe_transport_adapter(self):
        for value in [request(), request(snapshot_id=str(OLD)), request(column='id', literal='1')]:
            self.assertEqual(C.parse_request(C.canonical(value)), value)
            self.assertEqual(C.base_request(value), R.request('describe', table='smoke.fixture_v1'))
            self.assertEqual(C.discovery_sql(value), R.discovery_sql(C.base_request(value)))
            self.assertEqual(C.schema_sql(value), R.schema_sql(C.base_request(value)))
        value = request(); copied = C.normalize_request(value); copied['table'] = 'smoke.other'
        self.assertEqual(value['table'], 'smoke.fixture_v1')

    def test_extra_projection_limit_sql_and_bad_snapshot_are_refused(self):
        bad = [dict(request(), limit=1), dict(request(), columns=['id']), dict(request(), sql='COUNT(secret)'),
               dict(request(), command='select'), dict(request(), table='smoke.x;DROP TABLE x'),
               dict(request(), table='other.x'), {k:v for k,v in request().items() if k != 'where'}]
        bad += [dict(request(), snapshot_id=x) for x in [True, 1, 1.0, '0', '-1', '+1', '01', '1e2', str(2**63)]]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(C.ContractError): C.normalize_request(value)

    def test_json_duplicates_nonfinite_unicode_and_input_budget_are_refused(self):
        for raw in [b'{"command":"count","command":"count"}', b'{"command":NaN}', b'\xff',
                    b'', b' '*4097, b'[]', C.canonical(dict(request(), where=False))]:
            with self.subTest(raw=raw[:30]), self.assertRaises(C.ContractError): C.parse_request(raw)

    def test_filter_grammar_stays_existing_bounded_nonnull_subset(self):
        for literal in ['', 'NULL', '1e2', '+1', '01', ' 1', '1 ', "1'; SELECT", '１', '1\n', '1'*129,
                        '2026-02-29', '0000-01-01']:
            with self.subTest(literal=literal), self.assertRaises(C.ContractError):
                request(column='id', literal=literal)
        for where in [{}, [], {'column':'id','literal':'1','type':'bigint'}, {'column':'id','literal':1}]:
            with self.subTest(where=where), self.assertRaises(C.ContractError):
                C.normalize_request(dict(request(), where=where))

    def test_count_sql_pins_current_or_historical_without_limit(self):
        for value, selected in [(request(), HEAD), (request(snapshot_id=str(OLD)), OLD)]:
            sql = C.count_sql(value, inventory(), schema(), window(value))
            self.assertEqual(sql, 'SELECT COUNT(*) AS "row_count" FROM "iceberg"."smoke"."fixture_v1" '
                + f"FOR VERSION AS OF BIGINT '{selected}'")
            self.assertNotIn('LIMIT', sql)
            self.assertEqual(window(value)['identity']['current_snapshot_id'], str(HEAD))

    def test_all_snapshot_sizes_use_explicit_bigint_literal(self):
        for snapshot in [1, 2147483647, 2147483648, 2**63-1]:
            raw = metadata(); old = raw['metadata']['snapshots'][0]
            old['snapshot-id'] = snapshot
            raw['metadata']['snapshots'][1]['parent-snapshot-id'] = snapshot
            value = request(snapshot_id=str(snapshot)); observed = window(value, raw)
            self.assertIn(f"FOR VERSION AS OF BIGINT '{snapshot}'", C.count_sql(value, inventory(), schema(), observed))

    def test_filter_sql_reuses_exact_integer_decimal_date_and_boolean_compiler(self):
        cases = [('id','9007199254740993', '"id" = BIGINT \'9007199254740993\''),
                 ('amount','12345678901234567890123456789012.123456',
                  '"amount" = CAST(\'12345678901234567890123456789012.123456\' AS DECIMAL(38,6))'),
                 ('day','1900-01-01', '"day" = DATE \'1900-01-01\''),
                 ('ok','false', '"ok" = FALSE')]
        for column, literal, expected in cases:
            value = request(column=column, literal=literal, snapshot_id=str(OLD))
            with self.subTest(column=column):
                self.assertEqual(C.predicate_sql(value, schema()), expected)
                self.assertTrue(C.count_sql(value, inventory(), schema(), window(value)).endswith(' WHERE ' + expected))
                self.assertEqual(C.resolve_predicate(value, schema()), F.resolve_predicate(
                    dict(value, command='select', columns=[column], limit=1), schema()))

    def test_schema_bound_filter_refuses_unsupported_missing_and_lossy_values(self):
        for column, literal, code in [('name','1','FILTER_TYPE_UNSUPPORTED'), ('missing','1','FILTER_COLUMN_INVALID'),
                ('id','9223372036854775808','FILTER_VALUE_INVALID'), ('id','1.0','FILTER_VALUE_INVALID'),
                ('amount','0.0000001','FILTER_VALUE_INVALID'), ('ok','1','FILTER_VALUE_INVALID')]:
            value = request(column=column, literal=literal)
            with self.subTest(column=column, literal=literal), self.assertRaisesRegex(C.ContractError, code):
                C.count_sql(value, inventory(), schema(), window(value))

    def test_discovery_is_required_and_partial_inventory_is_not_empty_count(self):
        for changed in [dict(inventory(), complete=False), dict(inventory(), tables=[])]:
            with self.subTest(inventory=changed), self.assertRaises(C.ContractError):
                C.count_sql(request(), changed, schema(), window())

    def test_missing_snapshot_and_whole_schema_mismatch_are_refused(self):
        value = request(snapshot_id='42')
        with self.assertRaisesRegex(C.ContractError, 'SNAPSHOT_NOT_RETAINED'):
            C.count_sql(value, inventory(), schema(), window(value))
        raw = metadata(); historic = deepcopy(raw['metadata']['schemas'][0]); historic['schema-id'] = 1
        historic['fields'][-1]['type'] = 'long'
        raw['metadata']['schemas'].append(historic)
        raw['metadata']['snapshots'][0]['schema-id'] = 1
        value = request(snapshot_id=str(OLD), column='id', literal='1')
        with self.assertRaisesRegex(C.ContractError, 'SNAPSHOT_SCHEMA_UNSUPPORTED'):
            C.count_sql(value, inventory(), schema(), window(value, raw))

    def test_metadata_drift_is_not_accepted_as_same_count(self):
        before = window(); raw = metadata()
        raw['metadata-location'] = raw['metadata-location'].replace('/metadata/', '/metadata/moved-')
        after = window(raw=raw)
        with self.assertRaisesRegex(C.ContractError, 'SNAPSHOT_WINDOW_CHANGED'): C.verify_window(before, after)
        self.assertTrue(C.verify_window(before, deepcopy(before)))

    def test_no_current_snapshot_is_not_invented_as_zero(self):
        raw = metadata(); raw['metadata']['current-snapshot-id'] = -1
        raw['metadata']['snapshots'] = []; raw['metadata']['refs'] = {}
        observed = window(raw=raw)
        with self.assertRaisesRegex(C.ContractError, 'NO_CURRENT_SNAPSHOT'):
            C.count_sql(request(), inventory(), schema(), observed)
        with self.assertRaisesRegex(C.ContractError, 'NO_CURRENT_SNAPSHOT'):
            C.project_count(result(0), request(), schema(), observed)

    def test_exact_count_zero_and_int64_are_canonical_strings(self):
        for count in [0, 2, 3, 9007199254740993, 2**63-1]:
            with self.subTest(count=count):
                observed = public(count=count)
                self.assertEqual(observed['count'], str(count))
                self.assertIs(observed['complete'], True)
                self.assertEqual(set(observed), C.RESULT_KEYS)
                self.assertEqual(C.receipt_projection(observed), observed)

    def test_wire_count_rejects_null_bool_float_text_decimal_negative_and_overflow(self):
        for count in [None, False, True, -1, 2**63, 0.0, 1.0, '0', '01', Decimal('1'), float('nan')]:
            with self.subTest(count=count), self.assertRaises(C.ContractError): public(count=count)

    def test_wire_count_requires_one_bigint_cell_and_exact_alias(self):
        bad = [dict(result(), rows=[]), dict(result(), rows=[[1],[2]]), dict(result(), rows=[[1,2]]),
               dict(result(), rows=[1]), dict(result(), columns=['count']), dict(result(), column_types=['integer']),
               dict(result(), column_types=['double']), dict(result(), column_types=['decimal(38,0)'])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(C.ContractError):
                C.project_count(value, request(), schema(), window())

    def test_public_truthful_head_selected_snapshot_and_no_literal_or_rows(self):
        value = request(snapshot_id=str(OLD), column='id', literal='9007199254740993')
        observed = public(value, 1)
        self.assertEqual(observed['current_snapshot_id'], str(HEAD))
        self.assertEqual(observed['snapshot_id'], str(OLD))
        self.assertFalse(observed['selected_snapshot']['is_current'])
        self.assertEqual(observed['where'], {'column':'id','type':'bigint','operator':'eq'})
        text = C.canonical(observed).decode()
        for private in ['9007199254740993', 'literal', 'rows', 'SELECT', 'metadata.json', 's3://', 'not-public']:
            self.assertNotIn(private, text)
        copied = C.receipt_projection(observed); copied['selected_snapshot']['snapshot_id'] = '2'
        self.assertEqual(observed['selected_snapshot']['snapshot_id'], str(OLD))

    def test_public_noncanonical_count_and_wrong_request_binding_are_refused(self):
        for count in [None, True, 0, 1.0, '-0', '-1', '+1', '00', '01', '1.0', '1e0', '1\n', str(2**63)]:
            with self.subTest(count=count), self.assertRaises(C.ContractError):
                C.validate_public(dict(public(), count=count))
        for changed in [dict(public(), complete=1), dict(public(), schema_version=True),
                dict(public(), schema_sha256='x'), dict(public(), rows=[]), dict(public(), snapshot_id=str(OLD)),
                dict(public(), table='iceberg.smoke.other'), dict(public(), where={'column':'id','type':'bigint','operator':'eq'})]:
            with self.subTest(changed=changed), self.assertRaises(C.ContractError): C.validate_public(changed, request())

    def test_public_filter_type_shape_and_absence_are_validated(self):
        value = request(column='id', literal='1'); observed = public(value)
        for descriptor in [None, {'column':'id','type':'varchar','operator':'eq'},
                {'column':'id','type':'bigint','operator':'ne'}, {'column':'id','type':'bigint','operator':'eq','literal':'1'},
                {'column':'amount','type':'bigint','operator':'eq'}]:
            with self.subTest(descriptor=descriptor), self.assertRaises(C.ContractError):
                C.validate_public(dict(observed, where=descriptor), value)
        with self.assertRaises(C.ContractError): C.validate_public(observed, request())

    def test_old_reader_snapshot_filter_shapes_are_not_repurposed(self):
        for contract in [R, S, F]:
            with self.subTest(contract=contract.__name__), self.assertRaises(C.ContractError):
                contract.normalize_request(request())
        self.assertEqual(C.PROFILE, 'snapshot-count-reader-v4')
        self.assertEqual(window()['schema_version'], 2)


if __name__ == '__main__':
    unittest.main()
