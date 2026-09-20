"""Private synthetic fixtures and analytic fake models; no sklearn fit/API."""
from copy import deepcopy
from fractions import Fraction as F
import io
import json
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.mlflow import tune as U
from tests import test_notebook_lake_training_client as V


def request():
    return dict(schema_version=1, training_token=V.TOKEN)


def fitted(train, alpha):
    """Exact small normal-equation oracle for the fake fixture, not sklearn."""
    rows = [[F(str(value)) for value in row] for row in train]
    means = [sum(row[j] for row in rows) / len(rows) for j in range(4)]
    centered = [[row[j] - means[j] for j in range(4)] for row in rows]
    matrix = [[sum(row[j] * row[k] for row in centered) + (F(str(alpha)) if j == k else 0)
               for k in range(3)] + [sum(row[j] * row[3] for row in centered)] for j in range(3)]
    for j in range(3):
        pivot = matrix[j][j]
        matrix[j] = [x / pivot for x in matrix[j]]
        for k in range(3):
            if k != j:
                scale = matrix[k][j]
                matrix[k] = [a - scale * b for a, b in zip(matrix[k], matrix[j])]
    coefficients = [row[3] for row in matrix]
    return (dict(coefficients=list(map(float, coefficients)),
                 intercept=float(means[3] - sum(coefficients[j] * means[j] for j in range(3)))),
            dict(python='3.12.12', numpy='2.5.3', scikit_learn='1.9.1'))


class TuneTests(unittest.TestCase):
    def setUp(self):
        self.fixture = V.LakeTrainingReviewTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fit = patch.object(U.C, '_fit', side_effect=fitted).start()
        self.addCleanup(patch.stopall)

    def snapshot(self):
        paths = [self.fixture.receipt_path, self.fixture.workload_path, self.fixture.artifact,
                 self.fixture.state / 'store.json']
        return [(p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns, p.stat().st_ino) for p in paths]

    def test_nine_train_only_fold_fits_then_one_refit_with_stable_sources(self):
        before = self.snapshot()
        result = U.tune(request())
        self.assertEqual(self.fit.call_count, 10)
        partition = result['cv']['folds']
        validation = [i for fold in partition for i in fold['validation_indices']]
        self.assertEqual(len(validation), 48); self.assertEqual(set(validation), set(range(48)))
        rows = self.fixture.workload['dataset']['rows']
        for index, call in enumerate(self.fit.call_args_list[:9]):
            fold = partition[index % 3]
            self.assertEqual(len(fold['train_indices']), 32)
            self.assertEqual(len(fold['validation_indices']), 16)
            self.assertFalse(set(fold['train_indices']) & set(fold['validation_indices']))
            self.assertEqual(set(fold['train_indices']) | set(fold['validation_indices']), set(range(48)))
            self.assertEqual(call.args, ([rows[i] for i in fold['train_indices']], U.ALPHAS[index // 3]))
        self.assertEqual(self.fit.call_args_list[-1].args,
                         (rows[:48], result['cv']['selected_alpha']))
        self.assertEqual(result['candidate']['alpha'], 0.1)
        self.assertEqual(result['cv']['selected_alpha'], result['candidate']['alpha'])
        self.assertEqual([x['row_index'] for x in result['holdout']], list(range(48, 64)))
        self.assertEqual(result['identity'], self.fixture.public()['identity'])
        self.assertEqual(result['lake_source'], self.fixture.public()['lake_source'])
        self.assertEqual(result['request_sha256'], U.digest(request()))
        self.assertEqual(self.snapshot(), before); self.assertEqual(self.fixture.calls, [])
        self.assertLess(len(U.canonical(result)), U.MAX_PUBLIC_BYTES)

    def test_validation_replays_all_models_metrics_and_selection_without_fit(self):
        result = U.tune(request()); self.fit.reset_mock()
        self.assertEqual(U.validate_result(result, request()), result)
        self.fit.assert_not_called()
        for group in result['cv']['candidates']:
            self.assertAlmostEqual(group['mean_validation_mse'],
                sum(fold['validation_metrics']['mse'] for fold in group['folds']) / 3)

    def test_holdout_values_cannot_change_selection_or_refit(self):
        first = U.tune(request())
        saved = U.C._saved
        def altered(request, operation):
            def apply(workload, reference):
                workload = deepcopy(workload)
                for i in workload['split']['test_indices']:
                    workload['dataset']['rows'][i][3] += 10000
                return operation(workload, reference)
            return saved(request, apply)
        self.fit.reset_mock()
        with patch.object(U.C, '_saved', side_effect=altered):
            second = U.tune(request())
        self.assertEqual(first['cv'], second['cv'])
        self.assertEqual(first['candidate']['model'], second['candidate']['model'])
        self.assertNotEqual(first['candidate']['metrics']['test'], second['candidate']['metrics']['test'])
        self.assertEqual(self.fit.call_count, 10)

    def test_exact_tie_selects_smallest_alpha_and_shuffle_does_not_use_global_rng(self):
        values = [dict(alpha=a, mean_validation_mse=2.0) for a in U.ALPHAS]
        self.assertEqual(U.select_alpha(values), 0.1)
        values[1]['mean_validation_mse'] = 1.0
        self.assertEqual(U.select_alpha(values), 1.0)
        before = U.random.getstate()
        first = U.folds(list(range(48)))
        self.assertEqual(U.folds(list(range(48))), first)
        self.assertEqual(U.random.getstate(), before)
        self.assertNotEqual(first[0]['validation_indices'], list(range(16)))

    def test_saved_result_tampering_is_rejected_without_refit(self):
        original = U.tune(request()); self.fit.reset_mock()
        changes = [lambda v: v['cv'].update(seed=0),
                   lambda v: v['cv']['folds'][0]['validation_indices'].__setitem__(0, 63),
                   lambda v: v['cv']['candidates'][0]['folds'][0]['model'].update(intercept=0),
                   lambda v: v['cv']['candidates'][0]['folds'][0]['validation_metrics'].update(mse=0),
                   lambda v: v['cv']['candidates'][0].update(mean_validation_mse=0),
                   lambda v: v['cv'].update(selected_alpha=10),
                   lambda v: v['cv'].update(fit_count=9),
                   lambda v: v['candidate']['model'].update(intercept=0),
                   lambda v: v['holdout'][0].update(candidate_prediction=0),
                   lambda v: v.update(request_sha256='f' * 64)]
        for change in changes:
            bad = deepcopy(original); change(bad)
            with self.assertRaises(U.TuningError): U.validate_result(bad, request())
        self.fit.assert_not_called()

    def test_strict_request_before_any_source_read(self):
        self.assertEqual(U.parse_request(U.canonical(request())), request())
        values = [None, dict(request(), schema_version=True), dict(request(), alpha=1),
                  dict(request(), training_token='../x'), dict(request(), training_token=2)]
        with patch.object(U.C.T, '_sources', side_effect=AssertionError('source touched')) as read:
            for value in values:
                with self.assertRaises(U.TuningError): U.tune(value)
            for raw in (b'{}', b'{}\n{}', b'{"schema_version":1,"schema_version":1}',
                        b'{"x":NaN}', b' ' * (U.MAX_INPUT_BYTES + 1)):
                with self.assertRaises(U.TuningError): U.parse_request(raw)
            read.assert_not_called()

    def test_invalid_saved_source_stops_before_fit(self):
        self.fixture.workload_path.write_bytes(b'{}')
        with self.assertRaisesRegex(U.TuningError, 'TUNING_SOURCE_INVALID'): U.tune(request())
        self.fit.assert_not_called()

    def test_fit_failure_has_no_retry_or_refit(self):
        def fail(train, alpha):
            if self.fit.call_count == 4: raise RuntimeError('private estimator error')
            return fitted(train, alpha)
        self.fit.side_effect = fail
        with self.assertRaisesRegex(U.TuningError, 'TUNING_FIT_FAILED'): U.tune(request())
        self.assertEqual(self.fit.call_count, 4)

    def test_source_drift_and_original_cancellation_are_preserved(self):
        original = KeyboardInterrupt('first interrupt')
        def interrupt(train, alpha):
            self.fixture.workload_path.write_bytes(b'{}')
            raise original
        self.fit.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt) as caught: U.tune(request())
        self.assertIs(caught.exception, original); self.assertEqual(self.fit.call_count, 1)

    def test_source_drift_after_computation_refuses_result(self):
        def altered(train, alpha):
            result = fitted(train, alpha)
            if self.fit.call_count == 10: self.fixture.workload_path.write_bytes(b'{}')
            return result
        self.fit.side_effect = altered
        with self.assertRaisesRegex(U.TuningError, 'TUNING_SOURCE_INVALID'): U.tune(request())
        self.assertEqual(self.fit.call_count, 10)

    def test_cli_safe_success_failure_and_first_interrupt(self):
        output = io.StringIO()
        self.assertEqual(U.main(['--training-token', V.TOKEN], stdout=output), 0)
        value = json.loads(output.getvalue()); self.assertEqual(value['profile'], U.PROFILE)
        self.assertEqual(value['result'], 'passed'); self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.fit.reset_mock(); output = io.StringIO()
        self.assertEqual(U.main(['--training-token', V.TOKEN, '--alpha', '1'], stdout=output), 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'TUNING_ARGUMENTS_INVALID')
        self.fit.assert_not_called()
        self.fit.side_effect = RuntimeError('secret input and private path')
        output = io.StringIO(); self.assertEqual(U.main(['--training-token', V.TOKEN], stdout=output), 1)
        self.assertNotIn('secret input', output.getvalue())
        original = KeyboardInterrupt('original'); self.fit.side_effect = original
        output = Mock(); output.write.side_effect = OSError('write failure')
        with self.assertRaises(KeyboardInterrupt) as caught:
            U.main(['--training-token', V.TOKEN], stdout=output)
        self.assertIs(caught.exception, original)


if __name__ == '__main__':
    unittest.main()
