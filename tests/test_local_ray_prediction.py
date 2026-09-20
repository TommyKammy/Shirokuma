"""Two-actor algorithm with fake Ray; no Ray import, fit, or external calls."""
from copy import deepcopy
import os
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.ray import prediction as C
from tests.test_mlflow_predict import TOKEN, saved_review


class Method:
    def __init__(self, function): self.remote = function


class FakeRay:
    def __init__(self): self.options = None; self.owners = []; self.tamper = None
    def remote(self, **options):
        self.options = options
        def wrap(cls):
            def new(token, index):
                actor = cls(token, index); self.owners.append((token,index))
                def predict(request):
                    with patch.object(C.os, 'getpid', return_value=50001+index):
                        return actor.predict(request)
                return type('Actor', (), {'predict': Method(predict)})()
            return Method(new)
        return wrap
    def get(self, values, timeout):
        assert timeout == 60
        if self.tamper: self.tamper(values)
        return values


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.review, features = saved_review()
        self.workload = {'dataset':{'rows':[row+[0.0] for row in features]*4}}
        self.files = {'safe file':('bytes','identity',4096)}
        for obj,name,value in ((C.T,'_sources',(None,self.workload,None,self.files)),
                               (C.T,'_project',self.review),(C.T,'load_saved_review',self.review)):
            change=patch.object(obj,name,return_value=value); change.start();self.addCleanup(change.stop)
        change=patch.object(C.T,'_recheck',side_effect=lambda files,error:error)
        self.recheck=change.start();self.addCleanup(change.stop)
        self.ray=FakeRay()

    def test_two_distinct_actors_exact_32_split_identity_and_64_sequential_parity(self):
        inspected=Mock(); public=C.evaluate(TOKEN,self.ray,inspected)
        self.assertEqual(self.ray.options,{'num_cpus':1,'max_restarts':0,'max_task_retries':0})
        self.assertEqual(self.ray.owners,[(TOKEN,0),(TOKEN,1)])
        inspected.assert_called_once_with([50001,50002])
        self.assertEqual(public['predictions'],[r['prediction'] for r in self.review['holdout']]*4)
        self.assertEqual([a['row_offset'] for a in public['actors']],[0,32])
        self.assertEqual([a['row_count'] for a in public['actors']],[32,32])
        self.assertEqual(public['parity']['max_abs_error'],0.0)
        self.assertEqual(public['identity'],self.review['identity'])
        self.assertEqual(public['lake_source'],self.review['lake_source'])
        self.recheck.assert_called_once_with(self.files,None)
        for private in ('rows','model','coefficients','holdout'):
            self.assertNotIn(private,public)

    def test_changed_model_source_order_or_duplicate_pid_never_releases_result(self):
        mutations=[lambda p:p[1].update(pid=p[0]['pid']),
                   lambda p:p.reverse(),lambda p:p[1].update(partition=True),
                   lambda p:p[0].update(model_sha256='0'*64),
                   lambda p:p[0]['identity'].update(run_id='0'*32),
                   lambda p:p[0]['lake_source'].update(snapshot_id='1'),
                   lambda p:p[0].update(request_sha256='0'*64),
                   lambda p:p[0]['predictions'].__setitem__(0,float('nan')),
                   lambda p:p[0]['predictions'].__setitem__(0,True),
                   lambda p:p[0]['predictions'].__setitem__(0,123.0)]
        for mutate in mutations:
            self.ray=FakeRay();self.ray.tamper=mutate
            with self.subTest(mutation=mutate),self.assertRaises(C.RayPredictionError):
                C.evaluate(TOKEN,self.ray,Mock())

    def test_saved_file_change_after_actor_completion_rejects_success(self):
        self.recheck.side_effect=lambda files,error: ValueError('saved file changed')
        with self.assertRaisesRegex(C.RayPredictionError,'^RAY_SOURCE_INVALID$'):
            C.evaluate(TOKEN,self.ray,Mock())

    def test_first_cancel_survives_second_source_read_failure(self):
        first=KeyboardInterrupt('first')
        self.ray.get=Mock(side_effect=first)
        self.recheck.side_effect=lambda files,error:C.keep(error,ValueError('secondary'))
        with self.assertRaises(KeyboardInterrupt) as caught:C.evaluate(TOKEN,self.ray,Mock())
        self.assertIs(caught.exception,first)

    def test_invalid_profile_token_or_non64_source_does_not_submit_actors(self):
        with self.assertRaises(C.RayPredictionError):C.evaluate('invalid',self.ray,Mock())
        self.assertEqual(self.ray.owners,[])
        self.workload['dataset']['rows'].pop()
        with self.assertRaises(C.RayPredictionError):C.evaluate(TOKEN,self.ray,Mock())
        self.assertEqual(self.ray.owners,[])


if __name__=='__main__': unittest.main()
