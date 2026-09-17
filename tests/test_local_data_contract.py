"""Pure contract and independent-oracle tests; no runtime or model calls."""
import copy
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears/data_contract.py'
SPEC = importlib.util.spec_from_file_location('data_contract', PATH)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def plan(operation='name_lengths'):
    return {'schema_version': 1, 'source': 'fixture_v1', 'operation': operation}


class PlanContractTests(unittest.TestCase):
    def test_valid_plans_and_defensive_copy(self):
        for operation in contract.OPERATIONS:
            source = plan(operation)
            actual = contract.validate_plan(source)
            self.assertEqual(actual, source)
            self.assertIsNot(actual, source)

    def test_exact_keys_disallow_sql_shell_paths_and_output_columns(self):
        for key, value in {
            'sql': 'drop table retained', 'shell': 'rm -rf data',
            'path': '/tmp/output', 'namespace': 'retained',
            'columns': ['id', 'email'], 'alias': 'retained',
        }.items():
            with self.subTest(key=key), self.assertRaises(contract.ContractError):
                contract.validate_plan(dict(plan(), **{key: value}))
        for key in plan():
            bad = plan()
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(contract.ContractError):
                contract.validate_plan(bad)

    def test_types_version_source_and_operations_rejected(self):
        for key, values in {
            'schema_version': [True, False, 1.0, '1', 0, 2, None],
            'source': [True, None, [], {}, 'fixture_v2', 'fixture_v1; drop table x'],
            'operation': [True, None, [], {}, 'NAME_LENGTHS', 'select * from fixture_v1'],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(contract.ContractError):
                    contract.validate_plan(dict(plan(), **{key: value}))
        for value in [None, [], (), True, 'name_lengths']:
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.validate_plan(value)
        with self.assertRaises(contract.ContractError):
            contract.validate_plan(plan(), executable=1)

    def test_unsupported_can_be_classified_but_never_executed(self):
        unsupported = plan('unsupported')
        self.assertEqual(contract.validate_plan(unsupported)['operation'], 'unsupported')
        for execute in [lambda: contract.validate_plan(unsupported, executable=True),
                        lambda: contract.compile_select(unsupported),
                        lambda: contract.columns(unsupported),
                        lambda: contract.expected_rows(unsupported, [])]:
            with self.assertRaises(contract.ContractError):
                execute()

    def test_fixed_select_and_output_columns(self):
        expected = {
            'uppercase_names': ('select id, upper(name) as name_upper', ['id', 'name_upper']),
            'name_lengths': ('select id, length(name) as name_length', ['id', 'name_length']),
            'row_count': ('select count(*) as row_count', ['row_count']),
        }
        for operation, (selection, output_columns) in expected.items():
            with self.subTest(operation=operation):
                sql = contract.compile_select(plan(operation))
                self.assertEqual(sql, selection + "\nfrom {{ ref('stg_fixture') }}\n" +
                                 ('' if operation == 'row_count' else 'order by id\n'))
                self.assertEqual(contract.columns(plan(operation)), output_columns)
                changed = contract.columns(plan(operation))
                changed.append('private')
                self.assertEqual(contract.columns(plan(operation)), output_columns)

    def test_sql_never_contains_source_values(self):
        source = [[1, "x'); drop table retained; --"]]
        before = contract.compile_select(plan('uppercase_names'))
        self.assertEqual(contract.expected_rows(plan('uppercase_names'), source),
                         [[1, "X'); DROP TABLE RETAINED; --"]])
        self.assertEqual(contract.compile_select(plan('uppercase_names')), before)
        self.assertNotIn('retained', before)

    def test_schema_evidence_has_no_runtime_or_data_values_and_is_fresh(self):
        evidence = contract.schema_evidence()
        self.assertEqual(set(evidence), {'source', 'columns', 'constraints'})
        self.assertEqual(evidence['source'], 'fixture_v1')
        self.assertEqual(evidence['columns'], [{'name': 'id', 'type': 'bigint'},
                                              {'name': 'name', 'type': 'varchar'}])
        self.assertEqual(evidence['constraints']['uppercase_names'], 'ASCII names only')
        evidence['columns'][0]['name'] = 'email'
        self.assertEqual(contract.schema_evidence()['columns'][0]['name'], 'id')

    def test_golden_cases_have_explicit_semantics_and_languages(self):
        cases = contract.model_cases()
        self.assertGreaterEqual(len(cases), 5)
        self.assertEqual(len({case['case_id'] for case in cases}), len(cases))
        self.assertEqual({case['expected_operation'] for case in cases}, set(contract.OPERATIONS))
        self.assertGreaterEqual(sum(case['expected_operation'] == 'unsupported' for case in cases), 2)
        for case in cases:
            self.assertEqual(set(case), {'case_id', 'request', 'expected_operation'})
            self.assertTrue(0 < len(case['request']) <= 512)
            contract.validate_plan(plan(case['expected_operation']))
        self.assertTrue(any(not case['request'].isascii() for case in cases))
        self.assertTrue(any(case['request'].isascii() for case in cases))
        cases[0]['expected_operation'] = 'drop_table'
        self.assertEqual(contract.model_cases()[0]['expected_operation'], 'uppercase_names')


class IndependentOracleTests(unittest.TestCase):
    def test_supported_results_ordered_without_mutating_source(self):
        rows = [[9, 'shirokuma'], [-3, 'Polaris'], [2, '']]
        original = copy.deepcopy(rows)
        self.assertEqual(contract.expected_rows(plan('uppercase_names'), rows),
                         [[-3, 'POLARIS'], [2, ''], [9, 'SHIROKUMA']])
        self.assertEqual(contract.expected_rows(plan('name_lengths'), rows),
                         [[-3, 7], [2, 0], [9, 9]])
        self.assertEqual(contract.expected_rows(plan('row_count'), rows), [[3]])
        self.assertEqual(rows, original)

    def test_empty_sources(self):
        for operation in ('uppercase_names', 'name_lengths'):
            self.assertEqual(contract.expected_rows(plan(operation), []), [])
        self.assertEqual(contract.expected_rows(plan('row_count'), []), [[0]])

    def test_unicode_length_counts_codepoints_not_utf8_or_graphemes(self):
        rows = [[4, '🐻\u200d❄️'], [2, 'e\u0301'], [3, '🐻'], [1, 'しろくま']]
        self.assertEqual(contract.expected_rows(plan('name_lengths'), rows),
                         [[1, 4], [2, 2], [3, 1], [4, 4]])
        self.assertEqual(contract.expected_rows(plan('row_count'), rows), [[4]])

    def test_unicode_uppercase_rejected_instead_of_using_wrong_oracle(self):
        for name in ('straße', 'ı', 'しろくま', '🐻', 'é', 'e\u0301'):
            with self.subTest(name=name), self.assertRaisesRegex(contract.ContractError, 'ASCII'):
                contract.expected_rows(plan('uppercase_names'), [[1, name]])

    def test_row_shape_types_ranges_duplicates_and_unicode_validity(self):
        bad_sources = [
            None, (), {}, [[1]], [[1, 'name', 'email']], [(1, 'name')],
            [{'id': 1, 'name': 'name'}], [[True, 'name']], [[1.0, 'name']],
            [['1', 'name']], [[None, 'name']], [[2**63, 'name']],
            [[-(2**63)-1, 'name']], [[1, None]], [[1, b'name']], [[1, 3]],
            [[1, 'first'], [1, 'second']], [[1, '\ud800']], [[1, '\udfff']],
        ]
        for operation in ('uppercase_names', 'name_lengths', 'row_count'):
            for rows in bad_sources:
                with self.subTest(operation=operation, rows=repr(rows)), self.assertRaises(contract.ContractError):
                    contract.expected_rows(plan(operation), rows)
        self.assertEqual(contract.expected_rows(plan(), [[2**63-1, 'x'], [-(2**63), '']]),
                         [[-(2**63), 0], [2**63-1, 1]])

    def test_errors_do_not_leak_source_values(self):
        private = 'PRIVATE_VALUE_しろくま'
        with self.assertRaises(contract.ContractError) as caught:
            contract.expected_rows(plan('uppercase_names'), [[1, private]])
        self.assertNotIn(private, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
