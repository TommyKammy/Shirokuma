"""Saved private files and fake Ridge fits only: no SDK, network or actual fit."""
from copy import deepcopy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.mlflow import compare as C
from tests import test_notebook_lake_training_client as V


def request(alpha=10.0):
    return dict(schema_version=1, training_token=V.TOKEN, alpha=alpha)


def fitted(train, alpha):
    """Closed form for the existing orthogonal fake saved dataset."""
    assert len(train) == 48
    return (dict(coefficients=[x * 48 / (48 + alpha) for x in (2.5, -1.75, .5)], intercept=4.0),
            dict(python='3.12.12', numpy='2.5.3', scikit_learn='1.9.1'))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.fixture = V.LakeTrainingReviewTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        fit = patch.object(C, '_fit', side_effect=fitted)
        self.fit = fit.start()
        self.addCleanup(fit.stop)

    def snapshot(self):
        paths = [self.fixture.receipt_path, self.fixture.workload_path, self.fixture.artifact,
                 self.fixture.state / 'store.json']
        return [(p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns, p.stat().st_ino) for p in paths]

    def test_one_candidate_only_receives_saved_train_partition_and_keeps_source(self):
        before = self.snapshot()
        result = C.compare(request())
        self.assertEqual(self.fit.call_count, 1)
        self.assertEqual(self.fit.call_args.args, (self.fixture.workload['dataset']['rows'][:48], 10.0))
        self.assertEqual(result['identity'], self.fixture.public()['identity'])
        self.assertEqual(result['lake_source'], self.fixture.public()['lake_source'])
        self.assertEqual(result['request_sha256'], C.digest(request()))
        self.assertEqual(result['source_model_sha256'], C.digest(self.fixture.public()['model']))
        self.assertEqual(result['dataset_sha256'], self.fixture.workload['hashes']['dataset_sha256'])
        self.assertEqual(result['split_sha256'], self.fixture.workload['hashes']['split_sha256'])
        self.assertEqual(len(result['holdout']), 16)
        self.assertEqual([x['row_index'] for x in result['holdout']], list(range(48, 64)))
        for row in result['holdout']:
            self.assertAlmostEqual(row['prediction_delta'], row['candidate_prediction'] - row['reference_prediction'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.fixture.calls, [])

    def test_alpha_one_reproduces_saved_reference(self):
        result = C.compare(request(1))
        self.assertEqual(result['candidate']['alpha'], 1.0)
        for group in ('train', 'test'):
            for key in ('mae', 'rmse', 'r2'):
                self.assertAlmostEqual(result['candidate']['metrics'][group][key], result['reference']['metrics'][group][key])
        self.assertTrue(all(abs(row['prediction_delta']) < 1e-12 for row in result['holdout']))

    def test_request_bounds_strict_json_and_invalid_input_before_source(self):
        for alpha in (.001, 1000, 10):
            self.assertIs(type(C.parse_request(C.canonical(request(alpha)))['alpha']), float)
        self.assertEqual(C.digest(C.normalize_request(request(10))), C.digest(C.normalize_request(request(10.0))))
        cases = [dict(request(), schema_version=True), dict(request(), extra=1), dict(request(), training_token='../x')]
        cases += [request(x) for x in (True, None, '10', 0, -.1, .0009, 1001, float('inf'), float('nan'), 10**1000)]
        with patch.object(C.T, '_sources', side_effect=AssertionError('source touched')) as source:
            for value in cases:
                with self.subTest(value=str(value)[:60]), self.assertRaises(C.ComparisonError):
                    C.compare(value)
            for raw in (b'{}', b'{"alpha":1,"alpha":2}', b'{}\n{}', b' ' * (C.MAX_INPUT_BYTES + 1),
                        b'{"schema_version":1,"training_token":"' + V.TOKEN.encode() + b'","alpha":NaN}'):
                with self.assertRaises(C.ComparisonError):
                    C.parse_request(raw)
            source.assert_not_called()
        self.fit.assert_not_called()

    def test_parent_verification_replays_without_a_fit_and_rejects_changes(self):
        value = C.compare(request())
        self.fit.reset_mock()
        self.assertEqual(C.validate_result(value, request()), value)
        for change in ('alpha', 'identity', 'prediction', 'metrics', 'split', 'model', 'schema'):
            altered = deepcopy(value)
            if change == 'alpha': altered['candidate']['alpha'] = 20.0
            if change == 'identity': altered['identity']['run_id'] = 'e' * 32
            if change == 'prediction': altered['holdout'][0]['candidate_prediction'] += .01
            if change == 'metrics': altered['candidate']['metrics']['test']['rmse'] += .01
            if change == 'split': altered['split_sha256'] = '0' * 64
            if change == 'model': altered['candidate']['model']['coefficients'][0] += .01
            if change == 'schema': altered['schema_version'] = True
            with self.subTest(change=change), self.assertRaises(C.ComparisonError):
                C.validate_result(altered, request())
        with self.assertRaises(C.ComparisonError):
            C.validate_result(value, request(11))
        self.fit.assert_not_called()

    def test_invalid_saved_model_is_refused_before_fit(self):
        self.fixture.workload_path.write_bytes(b'{}')
        with self.assertRaisesRegex(C.ComparisonError, 'COMPARISON_SOURCE_INVALID'):
            C.compare(request())
        self.fit.assert_not_called()

    def test_source_changed_during_calculation_is_refused(self):
        def changed(train, alpha):
            self.fixture.workload_path.write_bytes(b'{}')
            return fitted(train, alpha)
        self.fit.side_effect = changed
        with self.assertRaisesRegex(C.ComparisonError, 'COMPARISON_SOURCE_INVALID'):
            C.compare(request())
        self.assertEqual(self.fit.call_count, 1)

    def test_first_cancellation_survives_source_change_and_output_failure(self):
        first = KeyboardInterrupt('original')
        def cancelled(*args):
            self.fixture.workload_path.write_bytes(b'{}')
            raise first
        self.fit.side_effect = cancelled
        class Broken(io.StringIO):
            def write(self, value): raise OSError('later output error')
        with self.assertRaises(KeyboardInterrupt) as caught:
            C.main(['--request-stdin'], stdin=io.BytesIO(C.canonical(request())), stdout=Broken())
        self.assertIs(caught.exception, first)

    def test_nonfinite_or_nonoptimal_candidate_rejected(self):
        model, versions = fitted([[]] * 48, 10)
        for invalid in (dict(model, intercept=float('nan')), dict(model, coefficients=[0.0, 0.0, 0.0]),
                        dict(model, coefficients=[True, 2.0, 3.0])):
            self.fit.return_value, self.fit.side_effect = (invalid, versions), None
            with self.assertRaisesRegex(C.ComparisonError, 'COMPARISON_MODEL_INVALID'):
                C.compare(request())

    def test_cli_success_and_fixed_failure_shape_without_exception_text(self):
        output = io.StringIO()
        self.assertEqual(C.main(['--request-stdin'], stdin=io.BytesIO(C.canonical(request())), stdout=output), 0)
        self.assertEqual(json.loads(output.getvalue())['result'], 'passed')
        self.fit.side_effect = OSError('secret detail')
        output = io.StringIO()
        self.assertEqual(C.main(['--request-stdin'], stdin=io.BytesIO(C.canonical(request())), stdout=output), 1)
        failure = json.loads(output.getvalue())
        self.assertEqual(failure, C.failure('COMPARISON_FIT_FAILED'))
        self.assertNotIn('secret', output.getvalue())
        output = io.StringIO()
        self.assertEqual(C.main([], stdin=io.BytesIO(), stdout=output), 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'COMPARISON_ARGUMENTS_INVALID')


class FitAdapterTests(unittest.TestCase):
    def test_fixed_sklearn_arguments_and_single_fit_with_fake_modules(self):
        rows = [[float(i), 2.0, 3.0, 4.0] for i in range(48)]
        class Array:
            def __getitem__(self, item):
                _, columns = item
                return [row[columns] for row in rows]
        array = Mock(return_value=Array())
        estimator = Mock(coef_=SimpleNamespace(tolist=lambda: [1., 2., 3.]), intercept_=4.)
        ridge = Mock(return_value=estimator)
        modules = {'numpy': SimpleNamespace(asarray=array, float64='float64', __version__='2.5.3'),
                   'sklearn': SimpleNamespace(__version__='1.9.1'),
                   'sklearn.linear_model': SimpleNamespace(Ridge=ridge)}
        with patch.dict('sys.modules', modules):
            model, versions = C._fit(rows, .1)
        array.assert_called_once_with(rows, dtype='float64')
        ridge.assert_called_once_with(alpha=.1, solver='svd', fit_intercept=True)
        estimator.fit.assert_called_once_with([row[:3] for row in rows], [row[3] for row in rows])
        self.assertEqual(model, dict(coefficients=[1., 2., 3.], intercept=4.))
        self.assertEqual(versions['scikit_learn'], '1.9.1')


if __name__ == '__main__':
    unittest.main()
