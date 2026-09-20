"""Self-contained Reader-shaped data and small fits; no lake or MLflow access."""
from copy import deepcopy
import json
import math
import unittest
from unittest.mock import patch

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, root_mean_squared_error, r2_score

from tools.local_experiment.mlflow import lake_training_workload as L
from tools.local_experiment.mlflow import training_workload as W
from tools.local_experiment.query_read import contract as C

TOKEN = 'b'*32


def observation():
    rows = []
    for i in reversed(range(64)):
        x1, x2, x3 = (i-31.5)/16, ((i*7)%19-9)/4, (-1)**i*(i%5)/3
        if i == 0:
            x3 = -0.0
        y = 4 + 2.5*x1 - 1.75*x2 + 0.5*x3 + 0.1*math.sin(i)
        rows.append([{'type':'bigint', 'value':str(i)},
                     *[{'type':'double', 'value':repr(float(value))} for value in (x1, x2, x3, y)]])
    return dict(schema_version=1, table='iceberg.local_experiment.training_'+'a'*32,
                table_uuid='00000000-0000-4000-8000-000000000001', schema_sha256='c'*64,
                snapshot_id='8088807033386546123',
                columns=[{'name':name, 'type':kind, 'nullable':True} for name, kind in
                         [('row_id', 'bigint'), ('x1', 'double'), ('x2', 'double'), ('x3', 'double'), ('target', 'double')]],
                rows=rows, row_count=64, fetched_row_count=64, truncated=False, requested_limit=100,
                ordering='unspecified', rows_sha256=C.digest(rows))


def input_value():
    return L.normalize_input(observation(), evidence_token=TOKEN)


def rehash(value):
    value['rows_sha256'] = C.digest(value['rows'])
    return value


class LakeTrainingWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = L.train(input_value())

    def test_deterministic_strict_json_result_and_original_reader_provenance(self):
        source = input_value(); before = deepcopy(source)
        value = L.train(source)
        self.assertEqual(source, before)
        self.assertEqual(L.canonical(value), L.canonical(self.result))
        self.assertIsNone(L.validate_result(json.loads(L.canonical(value))))
        self.assertEqual(set(value), W.RESULT_KEYS | {'lake_input'})
        self.assertEqual(value['lake_input'], source)
        self.assertEqual(value['lake_input']['observation']['rows'][0][0]['value'], '63')
        self.assertEqual(value['lake_input']['query_evidence_token'], TOKEN)
        self.assertNotIn('dataset_seed', value['params'])
        self.assertLess(len(L.canonical(value)), L.MAX_RESULT_BYTES)

    def test_input_is_defensively_copied_and_training_uses_actual_typed_values(self):
        public = observation()
        value = L.normalize_input(public, evidence_token=TOKEN)
        public['rows'][0][1]['value'] = '123.0'
        self.assertNotEqual(public, value['observation'])
        sorted_rows = sorted(value['observation']['rows'], key=lambda row:int(row[0]['value']))
        expected = [[float(cell['value']) for cell in row[1:]] for row in sorted_rows]
        with patch.object(W, '_dataset', side_effect=AssertionError('No replacement synthetic data')):
            result = L.train(value)
        self.assertEqual(L.canonical(result['dataset']['rows']), L.canonical(expected))
        self.assertEqual(math.copysign(1, result['dataset']['rows'][0][2]), -1)

    def test_unspecified_reader_order_does_not_change_numeric_training(self):
        public = observation(); public['rows'].reverse(); rehash(public)
        other = L.train(L.normalize_input(public, evidence_token=TOKEN))
        for field in ('dataset', 'split', 'model', 'metrics', 'baseline', 'test_predictions', 'hashes'):
            self.assertEqual(other[field], self.result[field])
        self.assertNotEqual(other['lake_input']['observation']['rows_sha256'],
                            self.result['lake_input']['observation']['rows_sha256'])
        self.assertNotEqual(L.digest(other), L.digest(self.result))

    def test_fixed_seed_split_is_disjoint_and_explicit_row_ids_are_retained(self):
        permutation = np.random.default_rng(1730).permutation(64).tolist()
        split = self.result['split']
        self.assertEqual(split['train_indices'], permutation[:48])
        self.assertEqual(split['test_indices'], permutation[48:])
        self.assertEqual(sorted(split['train_indices']+split['test_indices']), list(range(64)))
        self.assertTrue(set(split['train_indices']).isdisjoint(split['test_indices']))
        self.assertEqual(self.result['lake_input']['split_row_ids'], dict(train=permutation[:48], test=permutation[48:]))

    def test_ridge_fit_is_called_once_with_only_48_training_rows(self):
        fit = Ridge.fit; seen = []
        def observed(model, x, y, *args, **kwargs):
            seen.append((x.copy(), y.copy(), model.get_params()))
            return fit(model, x, y, *args, **kwargs)
        with patch.object(Ridge, 'fit', new=observed): result = L.train(input_value())
        self.assertEqual(len(seen), 1)
        rows = np.asarray(result['dataset']['rows']); indices = result['split']['train_indices']
        np.testing.assert_array_equal(seen[0][0], rows[indices, :3])
        np.testing.assert_array_equal(seen[0][1], rows[indices, 3])
        self.assertEqual(seen[0][0].shape, (48, 3))
        self.assertEqual((seen[0][2]['alpha'], seen[0][2]['solver'], seen[0][2]['fit_intercept']), (1.0, 'svd', True))

    def test_changing_only_holdout_targets_does_not_affect_fit_or_baseline(self):
        public = observation(); holdout = set(self.result['split']['test_indices'])
        for row in public['rows']:
            if int(row[0]['value']) in holdout:
                row[4]['value'] = repr(float(row[4]['value'])+5.0)
        result = L.train(L.normalize_input(rehash(public), evidence_token=TOKEN))
        self.assertEqual(result['model'], self.result['model'])
        self.assertEqual(result['baseline'], self.result['baseline'])
        self.assertEqual(result['metrics']['train'], self.result['metrics']['train'])
        self.assertNotEqual(result['metrics']['test'], self.result['metrics']['test'])

    def test_metrics_and_train_mean_baseline_match_independent_sklearn(self):
        value = self.result; rows = np.asarray(value['dataset']['rows'])
        train, test = value['split']['train_indices'], value['split']['test_indices']
        baseline = float(np.mean(rows[train, 3]))
        self.assertAlmostEqual(value['baseline']['prediction'], baseline, places=12)
        self.assertNotAlmostEqual(baseline, float(np.mean(rows[test, 3])), places=6)
        for group, indices in (('train', train), ('test', test), ('baseline_test', test)):
            selected = rows[indices]
            predictions = np.full(len(indices), baseline) if group == 'baseline_test' else (
                selected[:, :3]@np.asarray(value['model']['coefficients'])+value['model']['intercept'])
            expected = dict(mae=mean_absolute_error(selected[:, 3], predictions),
                            rmse=root_mean_squared_error(selected[:, 3], predictions),
                            r2=r2_score(selected[:, 3], predictions))
            for key, number in expected.items():
                self.assertAlmostEqual(value['metrics'][group][key], number, places=12)

    def test_validation_replays_json_coefficients_without_fitting_or_generating_data(self):
        value = json.loads(L.canonical(self.result))
        rows = np.asarray(value['dataset']['rows'])[value['split']['test_indices']]
        replay = rows[:, :3]@np.asarray(value['model']['coefficients'])+value['model']['intercept']
        np.testing.assert_allclose(replay, value['test_predictions'], rtol=1e-12, atol=1e-12)
        with patch.object(Ridge, 'fit', side_effect=AssertionError('validation must not train')), \
             patch.object(W, '_dataset', side_effect=AssertionError('no generated replacement')):
            L.validate_result(value)

    def test_incomplete_truncated_wrong_limit_or_unregistered_table_refused(self):
        variants = [('requested_limit', 64), ('row_count', 63), ('fetched_row_count', 65), ('truncated', True),
                    ('table', 'iceberg.smoke.fixture_v1'), ('table', 'iceberg.local_experiment.training_bad'),
                    ('ordering', 'sorted'), ('snapshot_id', None), ('schema_version', True)]
        for key, value in variants:
            public = observation(); public[key] = value
            with self.subTest(key=key), self.assertRaises(L.TrainingError):
                L.normalize_input(public, evidence_token=TOKEN)

    def test_row_identity_duplicate_missing_null_and_noncanonical_values_refused(self):
        mutations = [lambda value:value['rows'][0][0].update(value='62'),
                     lambda value:value['rows'][0][0].update(value='64'),
                     lambda value:value['rows'][0][0].update(value='-1'),
                     lambda value:value['rows'][0][0].update(value='063'),
                     lambda value:value['rows'][0][0].update(value=True),
                     lambda value:value['rows'][0][1].update(value=None),
                     lambda value:value['rows'][0][1].update(value='01.0'),
                     lambda value:value['rows'][0][1].update(value='NaN'),
                     lambda value:value['rows'][0][1].update(value='1e309'),
                     lambda value:value['rows'][0][1].update(value='1000001.0')]
        for index, mutate in enumerate(mutations):
            public = observation(); mutate(public); rehash(public)
            with self.subTest(index=index), self.assertRaises(L.TrainingError):
                L.normalize_input(public, evidence_token=TOKEN)

    def test_exact_schema_types_column_order_and_digest_required(self):
        for field in ('name', 'type', 'order', 'hash'):
            public = observation()
            if field == 'name': public['columns'][1]['name'] = 'unknown'
            elif field == 'type':
                public['columns'][1]['type'] = 'real'
                for row in public['rows']: row[1]['type'] = 'real'
                rehash(public)
            elif field == 'order': public['columns'][1], public['columns'][2] = public['columns'][2], public['columns'][1]
            else: public['rows_sha256'] = '0'*64
            with self.subTest(field=field), self.assertRaises(L.TrainingError):
                L.normalize_input(public, evidence_token=TOKEN)

    def test_fixture_profile_and_evidence_token_are_bounded(self):
        for token in ('', 'A'*32, '../'+TOKEN, True, 'a'*31, 'a'*33):
            with self.subTest(token=token), self.assertRaises(L.TrainingError):
                L.normalize_input(observation(), evidence_token=token)
        with self.assertRaises(L.TrainingError):
            L.normalize_input(observation(), evidence_token=TOKEN, fixture_profile='other')

    def test_numeric_dataset_cannot_be_replaced_even_with_new_digest(self):
        value = deepcopy(self.result); value['dataset']['rows'][0][0] += .1
        value['hashes']['dataset_sha256'] = L.digest(value['dataset'])
        with self.assertRaisesRegex(L.TrainingError, 'DATASET_CHANGED'): L.validate_result(value)
        value = deepcopy(self.result); value['dataset']['rows'][0][2] = 0.0
        value['hashes']['dataset_sha256'] = L.digest(value['dataset'])
        with self.assertRaisesRegex(L.TrainingError, 'DATASET_CHANGED'): L.validate_result(value)

    def test_split_order_and_provenance_split_ids_cannot_change(self):
        for path, key in (('split', 'train_indices'), ('split', 'test_indices'), ('lake_input', 'split_row_ids')):
            value = deepcopy(self.result)
            if path == 'split': value[path][key].reverse()
            else: value[path][key]['train'][0] = True
            value['hashes']['split_sha256'] = L.digest(value['split'])
            with self.subTest(path=path, key=key), self.assertRaises(L.TrainingError): L.validate_result(value)

    def test_model_fitted_to_all_rows_fails_train_only_optimum(self):
        value = deepcopy(self.result); rows = np.asarray(value['dataset']['rows'])
        leaked = Ridge(alpha=1.0, solver='svd').fit(rows[:, :3], rows[:, 3])
        value['model'] = dict(coefficients=leaked.coef_.tolist(), intercept=float(leaked.intercept_))
        with self.assertRaises(L.TrainingError): L.validate_result(value)

    def test_coefficient_prediction_metric_baseline_and_hash_mutations_refused(self):
        for location in ('coefficient', 'prediction', 'metric', 'baseline', 'hash'):
            value = deepcopy(self.result)
            if location == 'coefficient': value['model']['coefficients'][0] += .1
            elif location == 'prediction': value['test_predictions'][0] += .1
            elif location == 'metric': value['metrics']['test']['mae'] += .1
            elif location == 'baseline': value['baseline']['prediction'] += .1
            else: value['hashes']['dataset_sha256'] = '0'*64
            with self.subTest(location=location), self.assertRaises(L.TrainingError): L.validate_result(value)

    def test_unknown_keys_boolean_and_nonfinite_values_refused(self):
        paths = [(), ('lake_input',), ('lake_input', 'observation'), ('lake_input', 'split_row_ids'),
                 ('dataset',), ('split',), ('params',), ('model',), ('metrics',), ('metrics', 'test'),
                 ('baseline',), ('versions',), ('hashes',)]
        for path in paths:
            value = deepcopy(self.result); target = value
            for key in path: target = target[key]
            target['unknown'] = 'never disclose example'
            with self.subTest(path=path), self.assertRaises(L.TrainingError) as caught: L.validate_result(value)
            self.assertNotIn('never disclose example', str(caught.exception))
        for number in (True, float('nan'), float('inf'), -float('inf'), 10**400):
            for location in ('row', 'coefficient', 'prediction', 'metric', 'baseline'):
                value = deepcopy(self.result)
                if location == 'row': value['dataset']['rows'][0][0] = number
                elif location == 'coefficient': value['model']['coefficients'][0] = number
                elif location == 'prediction': value['test_predictions'][0] = number
                elif location == 'metric': value['metrics']['train']['mae'] = number
                else: value['baseline']['prediction'] = number
                with self.subTest(location=location, number=str(number)), self.assertRaises(L.TrainingError):
                    L.validate_result(value)

    def test_invalid_input_is_rejected_before_fit(self):
        value = input_value(); value['observation']['truncated'] = True
        with patch.object(Ridge, 'fit', side_effect=AssertionError('must not fit')):
            with self.assertRaises(L.TrainingError): L.train(value)


if __name__ == '__main__':
    unittest.main()
