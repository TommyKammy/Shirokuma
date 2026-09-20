"""Small real local fits and independent numeric checks; no MLflow/API writes."""
from copy import deepcopy
import json
import math
import unittest
from unittest.mock import patch

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, root_mean_squared_error, r2_score

from tools.local_experiment.mlflow import training_workload as T


class TrainingWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result=T.train()

    def test_training_is_deterministic_and_all_results_are_strict_json(self):
        other=T.train()
        self.assertEqual(T.canonical(other),T.canonical(self.result))
        restored=json.loads(T.canonical(other))
        self.assertIsNone(T.validate_result(restored))
        self.assertEqual(T.digest(restored),T.digest(self.result))
        self.assertLess(len(T.canonical(restored)),T.MAX_RESULT_BYTES)

    def test_fixed_split_covers_each_row_once_and_never_overlaps(self):
        split=self.result['split']
        expected=np.random.default_rng(1730).permutation(128).tolist()
        self.assertEqual(split['train_indices'],expected[:96])
        self.assertEqual(split['test_indices'],expected[96:])
        self.assertTrue(set(split['train_indices']).isdisjoint(split['test_indices']))
        self.assertEqual(sorted(split['train_indices']+split['test_indices']),list(range(128)))

    def test_fit_is_called_once_with_training_rows_only(self):
        original=Ridge.fit; observed=[]
        def fit(model,x,y,*args,**kwargs):
            observed.append((x.copy(),y.copy(),model.get_params()))
            return original(model,x,y,*args,**kwargs)
        with patch.object(Ridge,'fit',new=fit): value=T.train()
        self.assertEqual(len(observed),1)
        rows=np.asarray(value['dataset']['rows']); indices=value['split']['train_indices']
        np.testing.assert_array_equal(observed[0][0],rows[indices,:3])
        np.testing.assert_array_equal(observed[0][1],rows[indices,3])
        self.assertEqual(observed[0][0].shape,(96,3))
        self.assertEqual(observed[0][2]['alpha'],1.0); self.assertEqual(observed[0][2]['solver'],'svd')
        self.assertTrue(observed[0][2]['fit_intercept'])

    def test_baseline_uses_training_targets_and_is_evaluated_on_test_targets(self):
        rows=np.asarray(self.result['dataset']['rows'])
        train=rows[self.result['split']['train_indices'],3]; test=rows[self.result['split']['test_indices'],3]
        baseline=self.result['baseline']['prediction']
        self.assertAlmostEqual(baseline,float(np.mean(train)),places=12)
        self.assertNotAlmostEqual(baseline,float(np.mean(test)),places=7)
        actual=self.result['metrics']['baseline_test']; predictions=np.full(32,baseline)
        self.assertAlmostEqual(actual['mae'],mean_absolute_error(test,predictions),places=12)
        self.assertAlmostEqual(actual['rmse'],root_mean_squared_error(test,predictions),places=12)
        self.assertAlmostEqual(actual['r2'],r2_score(test,predictions),places=12)

    def test_json_coefficients_replay_predictions_without_loading_model_code(self):
        value=json.loads(T.canonical(self.result))
        rows=np.asarray(value['dataset']['rows'])[value['split']['test_indices']]
        predicted=rows[:,:3]@np.asarray(value['model']['coefficients'])+value['model']['intercept']
        np.testing.assert_allclose(predicted,value['test_predictions'],rtol=1e-12,atol=1e-12)
        with patch.object(Ridge,'fit',side_effect=AssertionError('validation must not train')):
            self.assertIsNone(T.validate_result(value))

    def test_train_and_test_metrics_match_independent_sklearn_recalculation(self):
        value=self.result; rows=np.asarray(value['dataset']['rows'])
        for partition in ('train','test'):
            with self.subTest(partition=partition):
                selected=rows[value['split'][partition+'_indices']]
                predicted=selected[:,:3]@np.asarray(value['model']['coefficients'])+value['model']['intercept']
                metrics=value['metrics'][partition]
                self.assertAlmostEqual(metrics['mae'],mean_absolute_error(selected[:,3],predicted),places=12)
                self.assertAlmostEqual(metrics['rmse'],root_mean_squared_error(selected[:,3],predicted),places=12)
                self.assertAlmostEqual(metrics['r2'],r2_score(selected[:,3],predicted),places=12)
        self.assertLess(value['metrics']['test']['rmse'],value['metrics']['baseline_test']['rmse'])

    def test_dataset_mutation_is_rejected_even_when_its_digest_is_replaced(self):
        value=deepcopy(self.result); value['dataset']['rows'][0][0]+=0.1
        value['hashes']['dataset_sha256']=T.digest(value['dataset'])
        with self.assertRaisesRegex(T.TrainingError,'DATASET_CHANGED'): T.validate_result(value)

    def test_overlapping_partition_and_changed_partition_order_are_rejected(self):
        for kind in ('overlap','order','cross_split','bool'):
            value=deepcopy(self.result)
            if kind=='overlap': value['split']['test_indices'][0]=value['split']['train_indices'][0]
            elif kind=='order': value['split']['train_indices'].reverse()
            elif kind=='cross_split':
                value['split']['test_indices'][0],value['split']['train_indices'][0]=(
                    value['split']['train_indices'][0],value['split']['test_indices'][0])
            else: value['split']['train_indices'][0]=True
            value['hashes']['split_sha256']=T.digest(value['split'])
            with self.subTest(kind=kind),self.assertRaises(T.TrainingError): T.validate_result(value)

    def test_model_fit_on_all_rows_fails_training_only_optimality_check(self):
        value=deepcopy(self.result); rows=np.asarray(value['dataset']['rows'])
        leaked=Ridge(alpha=1.0,solver='svd').fit(rows[:,:3],rows[:,3])
        value['model']={'coefficients':leaked.coef_.tolist(),'intercept':float(leaked.intercept_)}
        value['test_predictions']=leaked.predict(rows[value['split']['test_indices'],:3]).tolist()
        for partition in ('train','test'):
            selected=rows[value['split'][partition+'_indices']]; prediction=leaked.predict(selected[:,:3])
            value['metrics'][partition]={'mae':mean_absolute_error(selected[:,3],prediction),
                'rmse':root_mean_squared_error(selected[:,3],prediction),'r2':r2_score(selected[:,3],prediction)}
        with self.assertRaises(T.TrainingError): T.validate_result(value)

    def test_coefficient_and_intercept_changes_cannot_hide_behind_consistent_means(self):
        value=deepcopy(self.result)
        rows=np.asarray(value['dataset']['rows'])[value['split']['train_indices']]
        value['model']['coefficients'][0]+=0.1
        value['model']['intercept']-=0.1*float(np.mean(rows[:,0]))
        with self.assertRaisesRegex(T.TrainingError,'TRAIN_OPTIMUM'): T.validate_result(value)

    def test_prediction_metric_and_baseline_mutations_are_rejected(self):
        for kind in ('prediction','metric','baseline','hash'):
            value=deepcopy(self.result)
            if kind=='prediction': value['test_predictions'][0]+=0.01
            elif kind=='metric': value['metrics']['test']['mae']+=0.01
            elif kind=='baseline': value['baseline']['prediction']+=0.01
            else: value['hashes']['split_sha256']='0'*64
            with self.subTest(kind=kind),self.assertRaises(T.TrainingError): T.validate_result(value)

    def test_unknown_fields_at_every_contract_level_are_rejected(self):
        paths=[(),('dataset',),('split',),('params',),('model',),('baseline',),('metrics',),
               ('metrics','test'),('hashes',),('versions',)]
        for path in paths:
            value=deepcopy(self.result); target=value
            for key in path: target=target[key]
            target['unexpected']='private example'
            with self.subTest(path=path),self.assertRaises(T.TrainingError) as caught:
                T.validate_result(value)
            self.assertNotIn('private example',str(caught.exception))

    def test_nonfinite_huge_and_boolean_numbers_are_rejected(self):
        for bad in (True,float('nan'),float('inf'),float('-inf'),10**1000):
            for location in ('row','coefficient','intercept','prediction','metric','baseline'):
                value=deepcopy(self.result)
                if location=='row': value['dataset']['rows'][0][0]=bad
                elif location=='coefficient': value['model']['coefficients'][0]=bad
                elif location=='intercept': value['model']['intercept']=bad
                elif location=='prediction': value['test_predictions'][0]=bad
                elif location=='metric': value['metrics']['train']['mae']=bad
                else: value['baseline']['prediction']=bad
                with self.subTest(kind=type(bad),location=location),self.assertRaises(T.TrainingError):
                    T.validate_result(value)

    def test_schema_params_shape_versions_and_bounds_are_strict(self):
        mutations=[('schema_version',True),('profile','other'),('test_predictions',[]),
                   ('versions',{'python':'private/path','numpy':'2.5.3','scikit_learn':'1.9.1'})]
        for key,item in mutations:
            value=deepcopy(self.result); value[key]=item
            with self.subTest(key=key),self.assertRaises(T.TrainingError): T.validate_result(value)
        for key,item in [('alpha',True),('alpha',2.0),('solver','auto'),('fit_intercept',1),('dataset_seed',True)]:
            value=deepcopy(self.result); value['params'][key]=item
            with self.subTest(param=key),self.assertRaises(T.TrainingError): T.validate_result(value)
        value=deepcopy(self.result); value['model']['coefficients'][0]=1e308
        with self.assertRaises(T.TrainingError): T.validate_result(value)

    def test_canonical_and_digest_reject_non_json_values_and_ignore_dict_order(self):
        self.assertEqual(T.digest({'a':1,'b':2}),T.digest({'b':2,'a':1}))
        self.assertNotEqual(T.digest({'a':1}),T.digest({'a':2}))
        for bad in ({'x':float('nan')},{'x':object()},{'x':{1,2}}):
            with self.assertRaises(T.TrainingError): T.canonical(bad)


if __name__=='__main__': unittest.main()
