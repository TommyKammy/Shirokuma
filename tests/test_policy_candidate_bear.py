"""Real candidate workflow/OTel with fake model, Kubernetes, AH and Loki only."""
import asyncio
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/local_experiment/bears'))
import run_policy_candidate as R
from test_policy_candidate_observer import FakeKube,pod,TOKEN,PRIVATE
from test_local_sre_llm import local_config,EvaluationClient,EVALUATION


class CandidateRuntimeFixture:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name).resolve()/TOKEN
        self.kube=FakeKube();self.logs=[];self.calls=[];self.observers=[]
        self.raw=json.dumps(pod()).encode();self.mismatch=False;self.model_error=None
        self.agenthouse=EVALUATION.agenthouse_module()
        class Client(EvaluationClient):
            def query(self,sql):
                if ' WHERE event_id IN (' in sql:return json.dumps({'data':self.events})
                return super().query(sql)
        self.client=Client()
        # Keep actual observer while replacing only its transport boundary.
        original=R.CandidateObserver
        def factory(prepared):
            observer=original(prepared,self.kube);self.observers.append(observer);return observer
        def complete(phase,evidence):
            self.calls.append((phase,copy.deepcopy(evidence)))
            self.assertEqual(self.kube.requests,[])
            if self.model_error is not None:raise self.model_error
            expected=R.C.expected_plan(evidence,R.prepare(self.raw,TOKEN).host_truth())
            if self.mismatch:expected['classifications'][0]['violation_ids']=['P01']
            return dict(content=expected,prompt_tokens=20,completion_tokens=10,inference_cost_estimate=None,duration_ms=2)
        self.model=SimpleNamespace(complete=complete)
        self.loki=SimpleNamespace(push_record=self.logs.append,query_task=lambda task:self.logs)
        patches=[patch.object(R,'CandidateObserver',side_effect=factory),patch.object(R,'PolicyProvider',return_value=self.model),
            patch.object(R,'agenthouse_module',return_value=self.agenthouse),patch.object(self.agenthouse,'Client',return_value=self.client),
            patch.object(R,'module',return_value=self.loki)]
        for item in patches:item.start();self.addCleanup(item.stop)

    def run_workflow(self):
        return asyncio.run(R.run(local_config(),self.output,self.raw))


class CandidateBearTests(CandidateRuntimeFixture, unittest.TestCase):
    def test_allowed_actual_workflow_has_one_inference_one_dryrun_four_verified_spans(self):
        def request(body):
            pending=json.loads((self.output/'verification-progress.json').read_text())
            self.assertIsNotNone(pending['pending']);self.assertEqual(pending['request_count'],0)
            return SimpleNamespace(returncode=0,stdout=json.dumps(body),stderr='')
        self.kube.respond=request
        result=self.run_workflow()
        self.assertEqual(result['result'],'passed');self.assertTrue(result['workflow_complete'])
        self.assertEqual(result['saved_otel_spans_verified'],4);self.assertEqual(result['correlated_events'],4)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.kube.requests),1)
        evidence=self.calls[0][1]
        self.assertEqual(evidence,R.C.build_evidence([R.prepare(self.raw,TOKEN).candidate()],'native-baseline'))
        text='\n'.join(file.read_text() for file in self.output.iterdir())
        for secret in (PRIVATE,'private-pod','private-container',self.raw.decode()):self.assertNotIn(secret,text)
        self.assertFalse(result['authority_granted']);self.assertEqual(result['persisted_workloads'],0)
        self.assertTrue(all(file.stat().st_mode&0o777==0o600 for file in self.output.iterdir()))

    def test_owned_policy_denied_is_verified_without_authorizing_workload(self):
        self.raw=json.dumps(pod(privileged=True)).encode()
        self.kube.respond=lambda body:self.kube.denial(body,['P01'])
        result=self.run_workflow()
        self.assertEqual(result['result'],'passed');self.assertEqual(result['verification']['admission'],'denied')
        self.assertEqual(result['verification']['reported_violation_ids'],['P01']);self.assertFalse(result['authority_granted'])

    def test_invalid_input_precedes_all_baseline_model_and_audit_operations(self):
        self.raw=b'{"apiVersion":"v1","secret":"do-not-log"}'
        with self.assertRaises(ValueError):self.run_workflow()
        self.assertEqual(self.observers,[]);self.assertEqual(self.client.events,[]);self.assertEqual(self.calls,[])
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_overwritten_or_reinvoked(self):
        self.output.mkdir();protected=self.output/'original';protected.write_text('retained')
        with self.assertRaises(R.PolicyCandidateError):self.run_workflow()
        self.assertEqual(protected.read_text(),'retained');self.assertEqual(self.calls,[]);self.assertEqual(self.kube.reads,[])

    def test_bad_baseline_prevents_model_and_dryrun(self):
        self.kube.namespace['metadata']['labels']={}
        result=self.run_workflow()
        self.assertEqual(result['result'],'failed');self.assertEqual(self.calls,[]);self.assertEqual(self.kube.requests,[])
        self.assertFalse(result['workflow_complete'])

    def test_model_disagreement_is_blocked_and_never_sent(self):
        self.mismatch=True
        result=self.run_workflow()
        self.assertEqual(result['result'],'failed');self.assertEqual(len(self.calls),1);self.assertEqual(self.kube.requests,[])
        self.assertFalse(result['model_matched']);self.assertFalse((self.output/'classification.json').exists())
        model=next(r for r in result['audit_records'] if r['action_type']=='llm.policy_candidate.classify')
        self.assertEqual(model['status'],'blocked')

    def test_model_error_saves_usage_but_no_content_or_retry(self):
        self.model_error=R.ProviderError('private-model-message',dict(prompt_tokens=3,completion_tokens=None,
            duration_ms=4,content='private-model-body'))
        result=self.run_workflow();self.assertEqual(result['result'],'failed')
        model=next(r for r in result['audit_records'] if r['action_type']=='llm.policy_candidate.classify')
        self.assertEqual(model['prompt_tokens'],3);self.assertEqual(len(self.calls),1);self.assertEqual(self.kube.requests,[])
        self.assertNotIn('private-model-',json.dumps(result))

    def test_pending_save_failure_means_zero_dryrun(self):
        original=R.persist
        def fail(path,value):
            if path.name=='verification-progress.json' and value['pending'] is not None:raise OSError('private')
            original(path,value)
        with patch.object(R,'persist',side_effect=fail):result=self.run_workflow()
        self.assertEqual(result['result'],'failed');self.assertEqual(self.kube.requests,[])

    def test_unknown_dryrun_is_retained_without_retry(self):
        def fail(body):raise TimeoutError('private-response')
        self.kube.respond=fail
        result=self.run_workflow();self.assertEqual(result['result'],'failed')
        self.assertEqual(len(self.kube.requests),1);self.assertEqual(result['verification']['admission'],'unknown')
        self.assertIsNotNone(result['verification']['pending']);self.assertFalse(result['workflow_complete'])

    def test_audit_readback_mismatch_cannot_be_success(self):
        original=self.client.query
        def query(sql):
            if ' WHERE event_id IN (' in sql:return json.dumps({'data':[]})
            return original(sql)
        self.client.query=query
        result=self.run_workflow();self.assertEqual(result['result'],'failed');self.assertFalse(result['workflow_complete'])

    def test_saved_span_failure_cannot_be_success(self):
        with patch.object(R,'verify_saved_spans',side_effect=ValueError('private')):result=self.run_workflow()
        self.assertEqual(result['result'],'failed');self.assertFalse(result['workflow_complete'])

    def test_cancel_original_survives_audit_and_final_persist_failures(self):
        error=asyncio.CancelledError();self.model_error=error
        original=R.persist
        def write(path,value):
            if path.name=='result.json' and value['result']=='interrupted':raise OSError('secondary')
            original(path,value)
        with patch.object(R,'persist',side_effect=write),patch.object(self.loki,'push_record',side_effect=OSError('secondary')):
            # Initial observe audit must succeed, then model audit and final save fail.
            self.loki.push_record.side_effect=[None,OSError('secondary'),OSError('secondary')]
            with self.assertRaises(asyncio.CancelledError) as caught:self.run_workflow()
        self.assertIs(caught.exception,error);self.assertEqual(self.kube.requests,[])
        spans=[json.loads(line) for line in (self.output/'spans.jsonl').read_text().splitlines()]
        self.assertEqual(spans[-1]['status'],'ERROR')

    def test_cli_invalid_stdin_has_no_setup_or_raw_output(self):
        stdin=SimpleNamespace(buffer=io.BytesIO(b'{"private":"invalid"}'));stdout=io.StringIO()
        with patch.object(R.sys,'stdin',stdin),patch.object(R.sys,'stdout',stdout),\
             patch.object(R,'load_config',side_effect=AssertionError('must not configure')):
            code=R.main(['--candidate-stdin','--run-token',TOKEN])
        self.assertEqual(code,1);self.assertNotIn('private',stdout.getvalue());self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
