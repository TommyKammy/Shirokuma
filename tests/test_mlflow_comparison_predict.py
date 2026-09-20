"""Saved candidate prediction using fake private files; no services or fit."""
from copy import deepcopy
from decimal import Decimal
import io
import json
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import comparison_predict as P
from tests.test_notebook_lab_comparison_saved import COMPARISON_TOKEN, make_saved


def request(rows=None):
    return dict(schema_version=1, rows=rows if rows is not None else [dict(x1=.25, x2=-.75, x3=1.5)])


class ComparisonPredictionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_saved(self)

    def test_new_rows_predictions_and_candidate_identity_without_fit_or_process(self):
        rows = [dict(x1=.25, x2=-.75, x3=1.5), dict(x1=-2, x2=1, x3=0)]
        with patch.object(P.S.C, '_fit', side_effect=AssertionError('fit prohibited')), \
             patch.object(P.S.B.subprocess, 'run', side_effect=AssertionError('process query prohibited')), \
             patch.object(P.S.T, '_get', side_effect=AssertionError('HTTP prohibited')):
            value = P.predict(COMPARISON_TOKEN, request(rows))
        candidate = self.fixture.comparison['candidate']
        model = candidate['model']
        expected = [float(Decimal(str(model['intercept'])) + sum(Decimal(str(coefficient)) * Decimal(str(row[feature]))
                    for coefficient, feature in zip(model['coefficients'], ('x1', 'x2', 'x3')))) for row in rows]
        for actual, number in zip(value['predictions'], expected):
            self.assertAlmostEqual(actual, number, places=12)
        self.assertEqual(value['comparison_token'], COMPARISON_TOKEN)
        self.assertEqual(value['alpha'], 10.0)
        self.assertEqual(value['request_sha256'], P.digest(request(rows)))
        self.assertEqual(value['comparison_request_sha256'], self.fixture.comparison['request_sha256'])
        self.assertEqual(value['comparison_sha256'], P.digest(self.fixture.comparison))
        self.assertEqual(value['identity'], self.fixture.comparison['identity'])
        self.assertEqual(value['lake_source'], self.fixture.comparison['lake_source'])
        hashed = dict(algorithm='Ridge', alpha=10.0, solver='svd', fit_intercept=True,
                      feature_names=['x1', 'x2', 'x3'], **model)
        self.assertEqual(value['model_sha256'], P.digest(hashed))
        self.assertEqual(P.load_saved(COMPARISON_TOKEN), self.fixture.public)

    def test_saved_holdout_predictions_are_reproduced(self):
        saved = self.fixture.original.workload
        rows = [dict(zip(P.P.FEATURES, saved['dataset']['rows'][i][:3])) for i in saved['split']['test_indices']]
        actual = P.predict(COMPARISON_TOKEN, request(rows))['predictions']
        expected = [row['candidate_prediction'] for row in self.fixture.comparison['holdout']]
        self.assertEqual(actual, expected)

    def test_invalid_token_and_existing_input_bounds_before_saved_read(self):
        with patch.object(P, 'load_saved', side_effect=AssertionError('source read')) as load:
            for token in ('../x', '', True, None):
                with self.assertRaises(P.ComparisonPredictionError):
                    P.predict(token, request())
            cases = [dict(request(), schema_version=True), request([]), request([dict(x1=0, x2=0, x3=0)] * 65),
                     request([dict(x1=0, x2=True, x3=0)]), request([dict(x1=0, x2=0, x3=float('inf'))]),
                     request([dict(x1=0, x2=0, x3=0, target=0)]), dict(request(), alpha=1)]
            for value in cases:
                with self.assertRaisesRegex(P.ComparisonPredictionError, 'REQUEST_INVALID'):
                    P.predict(COMPARISON_TOKEN, value)
            for raw in (b'{"schema_version":1,"schema_version":1,"rows":[]}', b'{"rows":NaN}',
                        b' ' * (P.MAX_INPUT_BYTES + 1), b'{}\n{}'):
                with self.assertRaisesRegex(P.ComparisonPredictionError, 'REQUEST_INVALID'):
                    P.parse_request(raw)
            load.assert_not_called()

    def test_sixty_four_rows_and_overflow_without_partial_publication(self):
        self.assertEqual(len(P.predict(COMPARISON_TOKEN, request([dict(x1=0, x2=0, x3=0)] * 64))['predictions']), 64)
        output = io.StringIO()
        value = request([dict(x1=0, x2=0, x3=0), dict(x1=1e308, x2=-1e308, x3=1e308)])
        self.assertEqual(P.main(['--comparison-token', COMPARISON_TOKEN, '--request-stdin'],
                               stdin=io.BytesIO(P.canonical(value)), stdout=output), 1)
        self.assertEqual(json.loads(output.getvalue()), P.failure('COMPARISON_PREDICTION_NUMBER_INVALID'))

    def test_cli_success_and_invalid_saved_receipt_fixed_failure(self):
        output = io.StringIO()
        args = ['--comparison-token', COMPARISON_TOKEN, '--request-stdin']
        self.assertEqual(P.main(args, stdin=io.BytesIO(P.canonical(request())), stdout=output), 0)
        self.assertEqual(json.loads(output.getvalue()), P.predict(COMPARISON_TOKEN, request()))
        (self.fixture.directory / 'status.json').write_bytes(b'{}')
        output = io.StringIO()
        self.assertEqual(P.main(args, stdin=io.BytesIO(P.canonical(request())), stdout=output), 1)
        self.assertEqual(json.loads(output.getvalue()), P.failure('COMPARISON_PREDICTION_SOURCE_INVALID'))

    def test_first_cancellation_survives_output_failure(self):
        first = KeyboardInterrupt('original')
        class Broken(io.StringIO):
            def write(self, value): raise OSError('later')
        with patch.object(P, 'load_saved', side_effect=first):
            with self.assertRaises(KeyboardInterrupt) as caught:
                P.main(['--comparison-token', COMPARISON_TOKEN, '--request-stdin'],
                       stdin=io.BytesIO(P.canonical(request())), stdout=Broken())
        self.assertIs(caught.exception, first)


if __name__ == '__main__':
    unittest.main()
