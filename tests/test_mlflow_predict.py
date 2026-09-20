"""Fixed saved-model adapter and arithmetic tests; loader patched, no services."""
from copy import deepcopy
import hashlib
import io
import json
import math
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import predict as P

TOKEN = 'a' * 32


def request(rows=None):
    return {'schema_version':1, 'rows':rows if rows is not None else [{'x1':0, 'x2':0, 'x3':0}]}


def saved_review():
    """Hand-calculable public review; the real loader supplies the file binding."""
    features = [[float(i), float(i % 3), 1.0] for i in range(16)]
    expected = [4.0 + 2*x[0] - 3*x[1] + 0.5*x[2] for x in features]
    targets = [value + (0.1 if i % 2 else -0.1) for i, value in enumerate(expected)]
    def metrics(predictions):
        mean = sum(targets) / len(targets)
        squared = sum((a-b)**2 for a,b in zip(targets,predictions))
        return dict(mae=sum(abs(a-b) for a,b in zip(targets,predictions))/len(targets),
                    rmse=math.sqrt(squared/len(targets)), r2=1-squared/sum((a-mean)**2 for a in targets))
    public = dict(schema_version=1, profile=P.T.LAKE_PROFILE, scope=P.T.LAKE_SCOPE,
        identity=dict(attempt_token=TOKEN, run_id='b'*32, experiment_id='4', experiment=P.T.LAKE_EXPERIMENT,
                      store_id='00000000-0000-0000-0000-000000000001', workload_sha256='c'*64,
                      started_ms=1000, training_finished_ms=1100, finished_ms=1200),
        model=dict(algorithm='Ridge', alpha=1.0, solver='svd', feature_names=['x1','x2','x3'],
                   coefficients=[2.0,-3.0,0.5], intercept=4.0, train_rows=48, test_rows=16),
        metrics=dict(train=metrics(expected), test=metrics(expected), baseline_test=metrics([4.0]*16)),
        holdout=[dict(row_index=i, actual=actual, prediction=prediction, baseline_prediction=4.0,
                      residual=actual-prediction) for i,(actual,prediction) in enumerate(zip(targets,expected))],
        lake_source=dict(source_token='d'*32, fixture_profile=P.T.LAKE_FIXTURE_PROFILE,
                         table='iceberg.local_experiment.training_'+'d'*32,
                         table_uuid='00000000-0000-0000-0000-000000000002', snapshot_id='9007199254740993',
                         schema_sha256='e'*64, rows_sha256='f'*64, query_evidence_token='1'*32, ordering='unspecified'))
    P.T.validate_public(public)
    return public, features


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.review, self.holdout = saved_review()
        loader = patch.object(P.T, 'load_saved_review', return_value=self.review, create=True)
        self.loader = loader.start(); self.addCleanup(loader.stop)
        http = patch.object(P.T, '_get', side_effect=AssertionError('HTTP prohibited'))
        http.start(); self.addCleanup(http.stop)

    def test_zero_basis_hand_calculation_and_input_order(self):
        rows = [{'x1':0,'x2':0,'x3':0}, {'x1':1,'x2':0,'x3':0},
                {'x1':0,'x2':1,'x3':0}, {'x1':0,'x2':0,'x3':1},
                {'x3':4,'x2':-1,'x1':2}]
        value = request(rows); original = deepcopy(value)
        result = P.predict(TOKEN, value)
        self.assertEqual(result['predictions'], [4.0,6.0,1.0,4.5,13.0])
        self.assertEqual(value,original)
        self.assertEqual(set(result),P.PUBLIC_KEYS)
        self.assertEqual(result['identity'],self.review['identity'])
        self.assertEqual(result['lake_source'],self.review['lake_source'])
        self.assertEqual(result['model_sha256'],hashlib.sha256(P.canonical(self.review['model'])).hexdigest())
        self.assertEqual(result['request_sha256'],hashlib.sha256(P.canonical(value)).hexdigest())
        self.loader.assert_called_once_with(TOKEN,kind='iceberg')
        self.assertNotIn('rows',result)

    def test_saved_holdout_predictions_and_maximum_batch(self):
        rows = [dict(zip(P.FEATURES,row)) for row in self.holdout]
        actual = P.predict(TOKEN,request(rows))['predictions']
        self.assertEqual(actual,[row['prediction'] for row in self.review['holdout']])
        self.assertEqual(P.predict(TOKEN,request(rows*4))['predictions'],actual*4)

    def test_request_digest_normalizes_key_order_but_preserves_rows_and_numeric_types(self):
        one = b'{"rows":[{"x3":3,"x2":2,"x1":1}],"schema_version":1}'
        two = b'{"schema_version":1,"rows":[{"x1":1,"x2":2,"x3":3}]}'
        self.assertEqual(P.digest(P.parse_request(one)),P.digest(P.parse_request(two)))
        self.assertEqual(P.normalize_request(request())['rows'][0]['x1'],0)
        self.assertIs(type(P.normalize_request(request())['rows'][0]['x1']),int)

    def test_bad_schema_features_numbers_and_batch_are_rejected_before_loader(self):
        bad = [None, [], dict(request(),extra=1), dict(request(),schema_version=True), dict(request(),schema_version=2),
               request([]), request([{'x1':0,'x2':0,'x3':0}]*65), request([{'x1':0,'x2':0}]),
               request([{'x1':0,'x2':0,'x3':0,'target':1}])]
        for number in (True,False,None,'1',float('nan'),float('inf'),-float('inf'),10**400):
            bad.append(request([{'x1':number,'x2':0,'x3':0}]))
        for value in bad:
            with self.subTest(value_type=type(value).__name__),self.assertRaisesRegex(P.PredictionError,'REQUEST_INVALID'):
                P.predict(TOKEN,value)
        self.loader.assert_not_called()

    def test_duplicate_nan_overflow_invalid_utf8_and_byte_limit(self):
        bad = [b'',b'\xff',b' '* (P.MAX_INPUT_BYTES+1), b'[]',
               b'{"schema_version":1,"schema_version":1,"rows":[]}',
               b'{"schema_version":1,"rows":[{"x1":0,"x1":1,"x2":0,"x3":0}]}']
        for number in ('NaN','Infinity','-Infinity','1e999'):
            bad.append(('{"schema_version":1,"rows":[{"x1":'+number+',"x2":0,"x3":0}]}').encode())
        for raw in bad:
            with self.subTest(length=len(raw)),self.assertRaises(P.PredictionError): P.parse_request(raw)
        self.loader.assert_not_called()

    def test_nonfinite_product_and_fsum_overflow_never_release_partial_predictions(self):
        for row in ({'x1':1e308,'x2':0,'x3':0}, {'x1':5e307,'x2':-3e307,'x3':0}):
            with self.subTest(row=row),self.assertRaisesRegex(P.PredictionError,'NUMBER_INVALID'):
                P.predict(TOKEN,request([{'x1':0,'x2':0,'x3':0},row]))

    def test_invalid_saved_source_and_wrong_attempt_are_safe(self):
        for value in (None,dict(self.review,profile=P.T.PROFILE),
                      dict(self.review,identity=dict(self.review['identity'],attempt_token='2'*32))):
            self.loader.return_value=value
            with self.assertRaisesRegex(P.PredictionError,'^PREDICTION_SOURCE_INVALID$'): P.predict(TOKEN,request())
        self.loader.side_effect=ValueError('private credentials body')
        with self.assertRaisesRegex(P.PredictionError,'^PREDICTION_SOURCE_INVALID$'): P.predict(TOKEN,request())

    def test_main_exact_single_frame_and_safe_failure_output(self):
        for invalid in (False,True):
            output=io.StringIO()
            self.loader.side_effect=ValueError('private credentials body') if invalid else None
            code=P.main(['--training-token',TOKEN,'--request-stdin'],stdin=io.BytesIO(P.canonical(request())),stdout=output)
            self.assertEqual(code,1 if invalid else 0)
            self.assertEqual(len(output.getvalue().splitlines()),1)
            value=json.loads(output.getvalue()); self.assertEqual(set(value),P.PUBLIC_KEYS)
            self.assertNotIn('private credentials body',output.getvalue())
            if invalid:
                self.assertIsNone(value['predictions']); self.assertIsNone(value['identity'])
                self.assertEqual(value['error_code'],'PREDICTION_SOURCE_INVALID')
            else: self.assertEqual(value['predictions'],[4.0])

    def test_bad_cli_and_bad_stdin_do_not_load_saved_source(self):
        for args in ([],['--training-token','BAD','--request-stdin'],
                     ['--training-token',TOKEN,'--request-stdin','--extra'],
                     ['--token',TOKEN,'--request-stdin']):
            output=io.StringIO()
            self.assertEqual(P.main(args,stdin=io.BytesIO(b'{}'),stdout=output),1)
            self.assertEqual(json.loads(output.getvalue())['error_code'],'PREDICTION_ARGUMENTS_INVALID')
        output=io.StringIO()
        self.assertEqual(P.main(['--training-token',TOKEN,'--request-stdin'],stdin=io.BytesIO(b'{}'),stdout=output),1)
        self.assertEqual(json.loads(output.getvalue())['error_code'],'PREDICTION_REQUEST_INVALID')
        self.loader.assert_not_called()

    def test_first_cancellation_survives_output_failure(self):
        original=KeyboardInterrupt('first')
        self.loader.side_effect=original
        class Broken(io.StringIO):
            def write(self,value): raise OSError('secondary')
        with self.assertRaises(KeyboardInterrupt) as caught:
            P.main(['--training-token',TOKEN,'--request-stdin'],stdin=io.BytesIO(P.canonical(request())),stdout=Broken())
        self.assertIs(caught.exception,original)


class SavedReaderIntegrationTests(unittest.TestCase):
    def test_actual_shared_loader_and_main_read_only_private_fixture(self):
        from tests.test_notebook_lake_training_client import LakeTrainingReviewTests
        fixture=LakeTrainingReviewTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        files=[fixture.receipt_path,fixture.workload_path,fixture.artifact,fixture.state/'store.json']
        def snapshot():
            return [(path.read_bytes(),path.stat().st_mode,path.stat().st_mtime_ns,path.stat().st_ino) for path in files]
        before=snapshot()
        rows=[dict(zip(P.FEATURES,fixture.workload['dataset']['rows'][index][:3]))
              for index in fixture.workload['split']['test_indices']]
        output=io.StringIO()
        with patch.object(P.T,'_get',side_effect=AssertionError('HTTP prohibited')), \
             patch.object(P.T.N,'_write',side_effect=AssertionError('file write prohibited')):
            code=P.main(['--training-token',TOKEN,'--request-stdin'],
                        stdin=io.BytesIO(P.canonical(request(rows))),stdout=output)
        self.assertEqual(code,0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['identity']['attempt_token'],TOKEN)
        self.assertEqual(result['identity']['workload_sha256'],fixture.receipt['workload_sha256'])
        self.assertEqual(result['lake_source']['snapshot_id'],'12345')
        for actual,expected in zip(result['predictions'],fixture.workload['test_predictions']):
            self.assertAlmostEqual(actual,expected,places=12)
        self.assertEqual(snapshot(),before)


if __name__ == '__main__': unittest.main()
