"""Offline Assist orchestration: private fake receipts, no API/model/Reader."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook_assist import run as R
from tools.local_experiment.notebook import selection as S
from tools.local_experiment.llm.providers import ProviderError
from tests.test_notebook_assist_contract import INPUT, PROFILE, proposal
from tests.test_query_read_contract import identity, schema, result as row_result
from tests.test_local_notebook_client import encoded


class FakeModel:
    def __init__(self):
        self.calls, self.contexts = 0, []
        self.proposal, self.error = proposal(), None
    def request_body(self, phase, context):
        assert phase == 'notebook_preview'
        return {'model':'fake', 'context':deepcopy(context)}
    def complete(self, phase, context):
        self.calls += 1
        self.contexts.append(deepcopy(context))
        if self.error is not None: raise self.error
        return dict(content=deepcopy(self.proposal), prompt_tokens=None, completion_tokens=None,
                    inference_cost_estimate=None, duration_ms=1)


class FakeAudit:
    def __init__(self):
        self.events, self.callback = [], None
        self.start_hook = self.before_phase = self.after_phase = None
        self.close_error = self.closed_with = None
    def factory(self, token, output, profile, callback):
        self.callback = callback
        return self
    def start(self):
        self.events.append('start')
        self.callback({'state':'started'})
        if self.start_hook is not None: self.start_hook()
    def phase(self, name, call, *, inference=False, validate=None):
        self.events.append(name)
        if self.before_phase is not None: self.before_phase(name)
        value = call()
        if validate is not None: value = validate(value)
        if self.after_phase is not None: self.after_phase(name)
        self.callback({'state':'phase-complete','phase':name})
        return value
    def finish(self, **kwargs):
        self.events.append(('finish',kwargs))
        self.callback({'state':'finished',**kwargs})
    def close(self, original):
        self.events.append('close'); self.closed_with=original
        if self.close_error is not None: raise self.close_error


def saved_reader(directory, request, public, *, interrupted=False, cleanup=True):
    token='a'*32 if request['command']=='describe' else 'b'*32
    command='query '+request['command']
    R.N._write(directory/'reader-intent.json',dict(schema_version=1,command=command,request=request))
    frames=[dict(schema_version=1,event='allocated',command=command,evidence_token=token),
            dict(schema_version=1,event='result',command=command,evidence_token=token,
                 result='interrupted' if interrupted else 'passed',observation=None if interrupted else public,
                 error_code='INTERRUPTED' if interrupted else None,cleanup_verified=cleanup,
                 evidence=f'tmp/local-experiment/query-read/{token}/summary.json')]
    path=directory/'reader.stdout.jsonl'
    with path.open('xb') as stream:
        stream.write(encoded(frames)); os.fchmod(stream.fileno(),0o600)
    R.N._write(directory/'reader-status.json',dict(schema_version=1,
        state='interrupted' if interrupted else 'passed',pid=1234,pgid=1234,process_start='synthetic test process',
        evidence_token=token,exit_code=130 if interrupted else 0,child_exited=cleanup,cleanup_verified=cleanup,
        error_code='NOTEBOOK_INTERRUPTED' if interrupted else None))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve(); self.output=self.base/('c'*32); self.output.mkdir(mode=0o700)
        self.binary=self.base/'shirokuma'; self.binary.write_bytes(b'never executed')
        self.model,self.audit=FakeModel(),FakeAudit()
        self.sources={'fixed.py':'a'*64}
        self.describe_calls,self.asset_calls=[],[]
        self.describe_error=self.asset_drift=None
        self.describe_cleanup=self.asset_cleanup=True
        self.describe_environment=None
    def describe(self, table):
        self.describe_calls.append(table)
        self.describe_environment={key:os.environ.get(key) for key in R.A.ENV_KEYS}
        value=identity()
        saved_reader(self.output/'describe',{'command':'describe','table':table},value,
                     interrupted=self.describe_error is not None,cleanup=self.describe_cleanup)
        if self.describe_error is not None: raise self.describe_error
        return value
    def materialize(self, output, binary, *, request):
        self.asset_calls.append(deepcopy(request))
        assert R.A.verify_export(output,request=request)['request_sha256']==S.digest(request)
        values=[{'id':1,'name':'Shirokuma'},{'id':2,'name':'Polaris'}]
        public=R.N.C.project_rows(row_result(request,rows=[[row[name] for name in request['columns']] for row in values]),
                                 request,schema(),identity())
        if self.asset_drift is not None:
            key,value=self.asset_drift; public[key]=value
        saved_reader(output,request,public,cleanup=self.asset_cleanup)
        result=dict(schema_version=2,profile=R.A.SELECTED_PROFILE,result='passed',
            dagster_run_id='00000000-0000-4000-8000-000000000001',reader_cleanup_confirmed=True,error_code=None,
            request_sha256=S.digest(request),template_sha256=S.TEMPLATE_SHA256,
            assets=[dict(key=R.A.SELECTED_ASSETS[0],metadata=dict(row_count=public['row_count'],
                rows_sha256=public['rows_sha256'],snapshot_id=public['snapshot_id'],artifact='reader.stdout.jsonl',
                template_sha256=S.TEMPLATE_SHA256,request_sha256=S.digest(request))),
                dict(key=R.A.SELECTED_ASSETS[1],metadata=dict(run_count=0,evaluations_sha256='0'*64,
                    artifact='evaluations.json',cost=None,template_sha256=S.TEMPLATE_SHA256,request_sha256=S.digest(request)))])
        R.N._write(output/'result.json',result)
        return result
    def execute(self):
        return R.execute(self.output,self.binary,INPUT,environment_factory=lambda:(deepcopy(PROFILE),self.model),
                         audit_factory=self.audit.factory,describe=self.describe,materialize=self.materialize,
                         source_factory=lambda:deepcopy(self.sources))
    def summary(self):
        return json.loads((self.output/'summary.json').read_bytes())

    def test_accepted_selection_uses_private_export_one_model_and_matching_reader_identity(self):
        value=self.execute()
        expected={'command':'select','table':INPUT['table'],'columns':['name','id'],'limit':1}
        self.assertEqual(value['result'],'passed')
        self.assertEqual(self.describe_calls,[INPUT['table']]); self.assertEqual(self.asset_calls,[expected])
        self.assertEqual(self.model.calls,1)
        self.assertEqual(value['selection'],expected)
        self.assertTrue(value['describe_cleanup_confirmed'] and value['asset_cleanup_confirmed'])
        self.assertEqual(self.audit.events,['start',R.DESCRIBE,R.INFER,R.MATERIALIZE,('finish',{}),'close'])
        self.assertEqual(S.load_request(self.output/'assets/request.json',S.digest(expected)),expected)
        self.assertEqual(set(self.model.contexts[0]),{'table','request','columns'})
        self.assertNotIn(identity()['table_uuid'],json.dumps(self.model.contexts))
        for path in self.output.rglob('*'):
            self.assertEqual(path.stat().st_mode&0o777,0o700 if path.is_dir() else 0o600)

    def test_unsupported_is_normal_refusal_with_one_model_call_and_no_asset_export(self):
        self.model.proposal={'columns':None,'limit':None,'unsupported':True}
        value=self.execute()
        self.assertEqual(value['result'],'unsupported'); self.assertIsNone(value['selection'])
        self.assertEqual(self.model.calls,1); self.assertEqual(self.asset_calls,[])
        self.assertFalse((self.output/'assets').exists())
        self.assertIn(('finish',{'status':'blocked'}),self.audit.events)
        self.assertIsNone(self.audit.closed_with)

    def test_malformed_model_proposal_never_reaches_asset_or_retry(self):
        self.model.proposal=dict(proposal(),columns=['unknown'])
        with self.assertRaisesRegex(R.AssistRunError,'PROPOSAL_INVALID'): self.execute()
        self.assertEqual(self.model.calls,1); self.assertEqual(self.asset_calls,[])
        self.assertFalse((self.output/'assets').exists())
        self.assertEqual(self.summary()['result'],'failed')

    def test_model_transport_error_counts_attempt_and_preserves_no_asset_boundary(self):
        self.model.error=ProviderError('safe transport error',{'prompt_tokens':None})
        with self.assertRaises(R.AssistRunError): self.execute()
        self.assertEqual(self.summary()['model_calls'],1)
        self.assertEqual(self.model.calls,1); self.assertEqual(self.asset_calls,[])
        self.assertEqual(self.summary()['result'],'failed')

    def test_schema_drift_fails_after_materialization_without_erasing_business_result(self):
        self.asset_drift=('schema_sha256','f'*64)
        with self.assertRaisesRegex(R.AssistRunError,'IDENTITY_CHANGED'): self.execute()
        value=self.summary()
        self.assertEqual(value['result'],'failed'); self.assertEqual(value['asset_result']['result'],'passed')
        self.assertTrue(value['asset_cleanup_confirmed']); self.assertEqual(len(self.asset_calls),1)

    def test_table_replacement_uuid_drift_fails_after_materialization_without_retry(self):
        self.asset_drift=('table_uuid','00000000-0000-4000-8000-000000000099')
        with self.assertRaisesRegex(R.AssistRunError,'IDENTITY_CHANGED'): self.execute()
        self.assertEqual(self.summary()['asset_result']['result'],'passed')
        self.assertEqual(len(self.asset_calls),1)

    def test_newer_current_snapshot_is_allowed_when_uuid_and_schema_still_match(self):
        self.asset_drift=('snapshot_id',str(int(identity()['current_snapshot_id'])+1))
        self.assertEqual(self.execute()['result'],'passed')

    def test_input_changed_after_audit_start_prevents_describe_or_model(self):
        self.audit.start_hook=lambda:(self.output/'input.json').write_bytes(b'changed input')
        with self.assertRaisesRegex(R.AssistRunError,'INPUT_CHANGED'): self.execute()
        self.assertEqual(self.describe_calls,[]); self.assertEqual(self.model.calls,0); self.assertEqual(self.asset_calls,[])

    def test_source_changed_after_audit_start_prevents_describe_or_model(self):
        self.audit.start_hook=lambda:self.sources.update({'fixed.py':'b'*64})
        with self.assertRaisesRegex(R.AssistRunError,'SOURCE_CHANGED'): self.execute()
        self.assertEqual(self.describe_calls,[]); self.assertEqual(self.model.calls,0)

    def test_binary_changed_before_describe_is_refused_without_running_it(self):
        self.audit.start_hook=lambda:self.binary.write_bytes(b'changed binary')
        with self.assertRaisesRegex(R.AssistRunError,'SOURCE_CHANGED'): self.execute()
        self.assertEqual(self.describe_calls,[]); self.assertEqual(self.model.calls,0)

    def test_source_changed_after_inference_prevents_asset_execution(self):
        def change(phase):
            if phase==R.INFER: self.sources['fixed.py']='b'*64
        self.audit.after_phase=change
        with self.assertRaisesRegex(R.AssistRunError,'SOURCE_CHANGED'): self.execute()
        self.assertEqual(self.model.calls,1); self.assertEqual(self.asset_calls,[])

    def test_audit_failure_before_inference_prevents_model_and_materialization(self):
        def failure(phase):
            if phase==R.INFER: raise R.AssistRunError('NOTEBOOK_ASSIST_AUDIT_FAILED')
        self.audit.before_phase=failure
        with self.assertRaisesRegex(R.AssistRunError,'AUDIT_FAILED'): self.execute()
        self.assertEqual(self.describe_calls,[INPUT['table']]); self.assertEqual(self.model.calls,0)
        self.assertEqual(self.asset_calls,[])

    def test_audit_failure_after_inference_prevents_next_phase(self):
        def failure(phase):
            if phase==R.INFER: raise R.AssistRunError('NOTEBOOK_ASSIST_AUDIT_FAILED')
        self.audit.after_phase=failure
        with self.assertRaisesRegex(R.AssistRunError,'AUDIT_FAILED'): self.execute()
        self.assertEqual(self.model.calls,1); self.assertEqual(self.asset_calls,[])

    def test_model_response_save_failure_never_repeats_completed_model_call(self):
        original_write=R.N._write
        def write(path,value,**kwargs):
            if path.name=='model-response.json': raise OSError('response save failed')
            return original_write(path,value,**kwargs)
        with patch.object(R.N,'_write',side_effect=write):
            with self.assertRaises(R.AssistRunError): self.execute()
        self.assertEqual(self.model.calls,1); self.assertEqual(self.asset_calls,[])
        self.assertEqual(self.summary()['model_calls'],1)

    def test_malformed_describe_status_identity_is_not_cleanup_evidence(self):
        path=self.output/'describe'; path.mkdir(mode=0o700)
        saved_reader(path,{'command':'describe','table':INPUT['table']},identity())
        status=json.loads((path/'reader-status.json').read_bytes())
        for value in ([],True,'x'*65,'invalid\nstart'):
            with self.subTest(value=value):
                status['process_start']=value
                (path/'reader-status.json').write_bytes(R.N._canonical(status))
                self.assertFalse(R.describe_closed(path,INPUT['table']))

    def test_first_describe_cancel_survives_audit_close_and_final_save_failure(self):
        original=R.A.AssetsInterrupted(signal.SIGTERM); self.describe_error=original
        self.audit.close_error=OSError('secondary close')
        original_write=R.N._write
        def write(path,value,**kwargs):
            if path.name=='summary.json' and value.get('finished_at') is not None:
                raise OSError('secondary final save')
            return original_write(path,value,**kwargs)
        with patch.object(R.N,'_write',side_effect=write):
            with self.assertRaises(R.A.AssetsInterrupted) as caught: self.execute()
        self.assertIs(caught.exception,original); self.assertIs(self.audit.closed_with,original)
        self.assertEqual(self.model.calls,0); self.assertEqual(self.asset_calls,[])
        self.assertTrue(R.describe_closed(self.output/'describe',INPUT['table']))

    def test_unknown_describe_cleanup_never_advances_or_claims_success(self):
        self.describe_cleanup=False
        with self.assertRaises(R.AssistRunError): self.execute()
        self.assertEqual(self.summary()['result'],'unknown')
        self.assertEqual(self.model.calls,0); self.assertEqual(self.asset_calls,[])

    def test_unknown_asset_cleanup_overrides_business_pass_without_retry(self):
        self.asset_cleanup=False
        with self.assertRaises(R.AssistRunError): self.execute()
        value=self.summary()
        self.assertEqual(value['result'],'unknown'); self.assertFalse(value['asset_cleanup_confirmed'])
        self.assertEqual(value['asset_result']['result'],'passed'); self.assertEqual(len(self.asset_calls),1)

    def test_reader_environment_owns_two_values_clears_request_pair_and_restores_four(self):
        inherited={key:'preserved-'+str(i) for i,key in enumerate(R.A.ENV_KEYS)}
        with patch.dict(os.environ,inherited):
            self.execute()
            self.assertEqual({key:os.environ[key] for key in R.A.ENV_KEYS},inherited)
        self.assertEqual(self.describe_environment[R.A.ENV_KEYS[0]],str(self.output/'describe'))
        self.assertEqual(self.describe_environment[R.A.ENV_KEYS[1]],str(self.binary))
        self.assertIsNone(self.describe_environment[S.REQUEST_PATH_ENV]); self.assertIsNone(self.describe_environment[S.REQUEST_SHA_ENV])

    def test_environment_partial_setup_failure_restores_all_values_without_yield(self):
        inherited={key:'preserved' for key in R.A.ENV_KEYS}; yielded=False
        with patch.dict(os.environ,inherited):
            original_update=os.environ.update
            def fail(*args,**kwargs):
                original_update({R.A.ENV_KEYS[0]:'partial'})
                raise OSError('partial setup')
            with patch.object(os.environ,'update',side_effect=fail):
                with self.assertRaises(OSError):
                    with R.reader_environment(self.output,self.binary): yielded=True
            self.assertEqual({key:os.environ[key] for key in R.A.ENV_KEYS},inherited)
        self.assertFalse(yielded)

    def test_same_output_is_not_reexecuted(self):
        self.execute()
        with self.assertRaises(FileExistsError): self.execute()
        self.assertEqual(self.model.calls,1); self.assertEqual(len(self.asset_calls),1)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve(); self.state=self.base/'state'
    def test_invalid_stdin_and_unknown_arguments_stop_before_allocation_or_build(self):
        for args,raw in [([],b'{}'),(['--request-stdin'],b'{}'),(['--request-stdin'],b'x'*4097),
                         (['--request-stdin'],b'{"table":"smoke.fixture_v1","request":true}'),
                         (['--file'],json.dumps(INPUT).encode())]:
            with self.subTest(args=args),patch.object(R,'STATE',self.state), \
                 patch.object(R.subprocess,'run') as build,patch.object(R,'execute') as execute, \
                 patch('sys.stdout',io.StringIO()) as stdout:
                self.assertEqual(R.main(args,stdin=io.BytesIO(raw)),1)
                build.assert_not_called(); execute.assert_not_called()
                self.assertFalse(self.state.exists())
                self.assertIsNone(json.loads(stdout.getvalue())['evidence'])

    def run_main(self, *, outcome='passed', late=None, fail_owner=False, cleanup=True):
        observed=[]
        class End:
            def __enter__(self): return self
            def __exit__(self,*args):
                if late is not None: raise late
        def build(arguments,**kwargs):
            binary=Path(arguments[arguments.index('-o')+1]); binary.write_bytes(b'fake binary'); binary.chmod(0o700)
        def execute(output,binary,value):
            self.assertEqual(value,INPUT)
            report=dict(result=outcome,model_calls=1,describe_cleanup_confirmed=cleanup,asset_cleanup_confirmed=cleanup)
            R.N._write(output/'summary.json',report)
            observed.append((output,(output/'summary.json').read_bytes()))
            return report
        original_write=R.N._write
        def write(path,value,**kwargs):
            if fail_owner and path.name=='owner-result.json': raise OSError('secondary owner save')
            return original_write(path,value,**kwargs)
        with patch.object(R,'STATE',self.state),patch.object(R.A,'_signals',return_value=End()), \
             patch.object(R.subprocess,'run',side_effect=build),patch.object(R,'execute',side_effect=execute), \
             patch.object(R.N,'_write',side_effect=write),patch('sys.stdout',io.StringIO()) as stdout:
            code=R.main(['--request-stdin'],stdin=io.BytesIO(json.dumps(INPUT).encode()))
        return code,json.loads(stdout.getvalue()),observed[0]

    def test_main_owner_records_normal_unsupported_and_returns_distinct_exit_two(self):
        code,public,(output,before)=self.run_main(outcome='unsupported')
        self.assertEqual(code,2); self.assertEqual(public['result'],'unsupported')
        owner=json.loads((output/'owner-result.json').read_bytes())
        self.assertEqual((owner['result'],owner['summary_result']),('unsupported','unsupported'))
        self.assertTrue(owner['reader_cleanup_confirmed']); self.assertEqual(owner['model_calls'],1)
        self.assertTrue(public['evidence'].endswith('/owner-result.json'))
        self.assertEqual((output/'summary.json').read_bytes(),before)

    def test_main_post_business_cancel_preserves_summary_and_records_interrupted_owner(self):
        original=R.A.AssetsInterrupted(signal.SIGTERM)
        code,public,(output,before)=self.run_main(late=original)
        self.assertEqual(code,143); self.assertEqual(public['result'],'interrupted')
        self.assertEqual((output/'summary.json').read_bytes(),before)
        owner=json.loads((output/'owner-result.json').read_bytes())
        self.assertEqual((owner['result'],owner['summary_result']),('interrupted','passed'))
        self.assertEqual(owner['error_code'],'INTERRUPTED')

    def test_main_first_cancel_survives_secondary_owner_save_failure(self):
        code,public,(output,before)=self.run_main(late=R.A.AssetsInterrupted(signal.SIGTERM),fail_owner=True)
        self.assertEqual(code,143); self.assertEqual(public['result'],'interrupted')
        self.assertEqual(public['error_code'],'INTERRUPTED'); self.assertIsNone(public['evidence'])
        self.assertEqual((output/'summary.json').read_bytes(),before)
        self.assertFalse((output/'owner-result.json').exists())

    def test_main_cancel_normalizes_running_or_failed_summary_without_rewriting_it(self):
        for outcome in ('running','failed'):
            with self.subTest(outcome=outcome):
                code,public,(output,before)=self.run_main(outcome=outcome,late=R.A.AssetsInterrupted(signal.SIGTERM))
                self.assertEqual(code,143); self.assertEqual(public['result'],'interrupted')
                owner=json.loads((output/'owner-result.json').read_bytes())
                self.assertEqual((owner['result'],owner['summary_result']),('interrupted',outcome))
                self.assertEqual((output/'summary.json').read_bytes(),before)

    def test_main_unknown_cleanup_stays_unknown_through_cancel_and_owner_save_failure(self):
        for fail_owner in (False,True):
            with self.subTest(fail_owner=fail_owner):
                code,public,(output,before)=self.run_main(outcome='running',cleanup=False,
                    late=R.A.AssetsInterrupted(signal.SIGTERM),fail_owner=fail_owner)
                self.assertEqual(code,143); self.assertEqual(public['result'],'unknown')
                self.assertEqual(public['error_code'],'INTERRUPTED'); self.assertFalse(public['reader_cleanup_confirmed'])
                self.assertEqual((output/'summary.json').read_bytes(),before)

    def test_main_ordinary_owner_failure_overrides_prior_unsupported_result(self):
        code,public,(output,before)=self.run_main(outcome='unsupported',late=OSError('late ordinary failure'))
        self.assertEqual(code,1); self.assertEqual(public['result'],'failed')
        owner=json.loads((output/'owner-result.json').read_bytes())
        self.assertEqual((owner['result'],owner['summary_result']),('failed','unsupported'))
        self.assertEqual((output/'summary.json').read_bytes(),before)


if __name__=='__main__': unittest.main()
