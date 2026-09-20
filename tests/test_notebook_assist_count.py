"""COUNT proposals, direct Reader orchestration and audit, using only fakes."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_assist import contract as C, count as K, run as R, audit as AU
from tools.local_experiment.notebook_assist.provider import CountNotebookAssistProvider, NotebookAssistProvider
from tools.local_experiment.llm.providers import ProviderError
from tests import test_notebook_assist_runtime as V
from tests.test_notebook_assist_contract import PROFILE, context
from tests.test_notebook_assist_audit import FakeHouse, FakeLoki
from tests.test_notebook_count_client import frames
from tests.test_local_notebook_client import encoded
from tests.test_local_query_count import public

INPUT = {'table':'smoke.fixture_v1','request':'idが1の件数を数えて'}


def proposal(where=None):
    return {'where':where, 'unsupported':False}


class CountContractTests(unittest.TestCase):
    def test_total_and_single_typed_equality_have_only_host_current_table(self):
        for where in (None, {'column':'id','literal':'1'}):
            value=proposal(where);before=deepcopy(value)
            self.assertEqual(K.validate_proposal(value,context()),
                {'command':'count','table':INPUT['table'],'snapshot_id':None,'where':where})
            self.assertEqual(value,before)

    def test_supported_types_reuse_exact_predicate_compiler(self):
        for kind,literal in [('integer','-2147483648'),('bigint','9223372036854775807'),
                             ('decimal(8,2)','123.45'),('date','2024-02-29'),('boolean','false')]:
            ctx=context();ctx['columns'][0]['type']=kind
            value=proposal({'column':'id','literal':literal})
            with self.subTest(kind=kind):self.assertEqual(K.validate_proposal(value,ctx)['where'],value['where'])

    def test_invalid_equality_is_not_silently_changed_to_all_rows(self):
        for where in ({},[],{'column':'missing','literal':'1'},{'column':'id','literal':None},
                      {'column':'id','literal':True},{'column':'id','literal':1},
                      {'column':'name','literal':'1'},{'column':'id','literal':'1 OR 1=1'},
                      {'column':'id','literal':'1','operator':'gt'}):
            with self.subTest(where=where),self.assertRaises(C.AssistError):K.validate_proposal(proposal(where),context())
        for kind,literal in [('integer','2147483648'),('decimal(4,2)','1.234'),('date','2025-02-29'),('double','1')]:
            ctx=context();ctx['columns'][0]['type']=kind
            with self.subTest(kind=kind),self.assertRaises(C.AssistError):
                K.validate_proposal(proposal({'column':'id','literal':literal}),ctx)

    def test_unsupported_requires_explicit_null_and_extra_executable_fields_rejected(self):
        self.assertIsNone(K.validate_proposal({'where':None,'unsupported':True},context()))
        for value in ({'where':{},'unsupported':True},{'unsupported':False},
                      *[{**proposal(),key:value} for key,value in [('table','smoke.other'),('sql','SELECT 1'),
                           ('columns',['id']),('snapshot_id','1'),('limit',10),('unsupported',0)]]):
            with self.subTest(value=value),self.assertRaises(C.AssistError):K.validate_proposal(value,context())

    def test_provider_keeps_schema_only_and_separate_phase_with_no_transport_change(self):
        provider=CountNotebookAssistProvider(PROFILE)
        body=provider.request_body(K.PHASE,context())
        self.assertEqual(body['messages'][0]['content'],K.INSTRUCTION)
        self.assertEqual(json.loads(body['messages'][1]['content']),context())
        self.assertEqual(body['temperature'],0);self.assertFalse(body['stream'])
        for phase in ('notebook_preview','notebook_filtered_preview'):
            with self.assertRaises(ProviderError):provider.request_body(phase,context())
        with self.assertRaises(ProviderError):NotebookAssistProvider(PROFILE).request_body(K.PHASE,context())


class CountModel(V.FakeModel):
    def __init__(self):
        super().__init__();self.proposal=proposal({'column':'id','literal':'1'})
    def request_body(self,phase,context):
        assert phase==K.PHASE
        return {'model':'fake','context':deepcopy(context)}


class CountRuntimeTests(unittest.TestCase):
    setUp = V.RuntimeTests.setUp
    describe = V.RuntimeTests.describe
    summary = V.RuntimeTests.summary

    def invoke(self, **kwargs):
        if not isinstance(self.model,CountModel):self.model=CountModel()
        return R.execute(self.output,self.binary,INPUT,count=True,
            environment_factory=lambda:(PROFILE,self.model),audit_factory=self.audit.factory,
            describe=self.describe,count_reader=kwargs.pop('count_reader',self.read_count),
            source_factory=lambda:deepcopy(self.sources),**kwargs)

    def read_count(self, request):
        self.asset_calls.append(deepcopy(request))
        result=public(request)
        identity=V.identity()
        result.update(table_uuid=identity['table_uuid'],schema_sha256=identity['schema_sha256'])
        if self.asset_drift is not None:self.asset_drift(result)
        directory=self.output/'count'
        R.N._write(directory/'reader-intent.json',{'schema_version':1,'command':'query count','request':request})
        saved=frames(request,cleanup=self.asset_cleanup);saved[-1]['observation']=result
        path=directory/'reader.stdout.jsonl';path.write_bytes(encoded(saved));path.chmod(0o600)
        R.N._write(directory/'reader-status.json',dict(schema_version=1,state='passed',pid=1234,pgid=1234,
            process_start='synthetic',evidence_token=saved[0]['evidence_token'],exit_code=0,child_exited=True,
            cleanup_verified=self.asset_cleanup,error_code=None))
        return result

    def test_one_model_and_one_count_no_assets_with_seal_and_current_head_allowed(self):
        with patch.object(R.A,'execute',side_effect=AssertionError('no Dagster')), \
                patch.object(R.A,'export',side_effect=AssertionError('no Notebook')):
            got=self.invoke()
        self.assertEqual(got['result'],'passed');self.assertEqual(got['profile'],K.PROFILE)
        self.assertEqual(got['schema_version'],3);self.assertEqual(self.model.calls,1);self.assertEqual(len(self.asset_calls),1)
        self.assertEqual(got['count_result']['count'],'2');self.assertTrue(got['count_cleanup_confirmed'])
        self.assertNotEqual(got['description']['current_snapshot_id'],got['count_result']['current_snapshot_id'])
        self.assertNotIn('asset_result',got);self.assertFalse((self.output/'assets').exists())
        self.assertEqual(self.audit.events[1:4],[K.DESCRIBE,K.INFERENCE,K.COUNT])
        seal=json.loads((self.output/'execution-intent.json').read_bytes())
        self.assertEqual(seal['source_hashes'],got['source_hashes'])
        self.assertTrue(R.count_closed(self.output/'count',got['selection']))
        self.sources={'new-version.py':'b'*64}
        self.assertTrue(R.count_closed(self.output/'count',got['selection']))
        self.assertEqual(set(self.model.contexts[0]),{'table','request','columns'})

    def test_total_and_zero_are_verified_strings(self):
        self.model=CountModel();self.model.proposal=proposal()
        self.asset_drift=lambda result:result.update(count='0')
        got=self.invoke();self.assertEqual(got['count_result']['count'],'0')
        self.assertIsNone(got['count_result']['where']);self.assertIsNone(got['selection']['snapshot_id'])

    def test_explicit_unsupported_has_no_count_or_assets_and_clean_closure(self):
        self.model=CountModel();self.model.proposal={'where':None,'unsupported':True}
        got=self.invoke()
        self.assertEqual(got['result'],'unsupported');self.assertEqual(self.model.calls,1)
        self.assertEqual(self.asset_calls,[]);self.assertIsNone(got['count_result'])
        self.assertTrue(got['count_cleanup_confirmed']);self.assertFalse((self.output/'count').exists())

    def test_malformed_proposal_does_not_execute_count(self):
        self.model=CountModel();self.model.proposal={'where':{},'unsupported':False}
        with self.assertRaisesRegex(R.AssistRunError,'PROPOSAL_INVALID'):self.invoke()
        self.assertEqual(self.model.calls,1);self.assertEqual(self.asset_calls,[])
        self.assertTrue(self.summary()['count_cleanup_confirmed']);self.assertIsNone(self.summary()['count_result'])

    def test_table_uuid_drift_refuses_public_but_preserves_cleanup(self):
        self.asset_drift=lambda result:result.update(table_uuid='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
        with self.assertRaisesRegex(R.AssistRunError,'IDENTITY_CHANGED'):self.invoke()
        got=self.summary();self.assertIsNone(got['count_result']);self.assertTrue(got['count_cleanup_confirmed'])

    def test_schema_drift_refuses_public_but_preserves_cleanup(self):
        self.asset_drift=lambda result:result.update(schema_sha256='b'*64)
        with self.assertRaisesRegex(R.AssistRunError,'IDENTITY_CHANGED'):self.invoke()
        self.assertIsNone(self.summary()['count_result']);self.assertTrue(self.summary()['count_cleanup_confirmed'])

    def test_source_change_after_inference_prevents_count_without_retry(self):
        def change(phase):
            if phase==K.INFERENCE:self.sources={'changed.py':'b'*64}
        self.audit.after_phase=change
        with self.assertRaisesRegex(R.AssistRunError,'SOURCE_CHANGED'):self.invoke()
        self.assertEqual(self.model.calls,1);self.assertEqual(self.asset_calls,[])
        self.assertTrue(self.summary()['count_cleanup_confirmed'])

    def test_predicate_type_binds_to_full_describe_schema(self):
        self.asset_drift=lambda result:result['where'].update(type='integer')
        with self.assertRaisesRegex(R.AssistRunError,'IDENTITY_CHANGED'):self.invoke()
        self.assertIsNone(self.summary()['count_result']);self.assertTrue(self.summary()['count_cleanup_confirmed'])

    def test_count_cleanup_unknown_blocks_success_and_never_fabricates_zero(self):
        self.asset_cleanup=False
        with self.assertRaises(R.AssistRunError):self.invoke()
        got=self.summary();self.assertEqual(got['result'],'unknown')
        self.assertFalse(got['count_cleanup_confirmed']);self.assertIsNone(got['count_result'])
        self.assertEqual(len(self.asset_calls),1)

    def test_first_cancel_survives_secondary_audit_failure_without_retry(self):
        error=KeyboardInterrupt();self.audit.close_error=RuntimeError('secondary')
        def interrupted(request):
            self.read_count(request)
            raise error
        with self.assertRaises(KeyboardInterrupt) as got:self.invoke(count_reader=interrupted)
        self.assertIs(got.exception,error);self.assertEqual(len(self.asset_calls),1)
        self.assertEqual(self.summary()['result'],'interrupted');self.assertTrue(self.summary()['count_cleanup_confirmed'])

    def test_count_sources_include_real_count_filter_and_shared_reader_dependencies(self):
        sources=R.sources(count=True)
        for path in ('query_count/run.py','query_count/contract.py','query_filter/contract.py',
                     'query_read/client.py','query_snapshots/observe.py','notebook_assist/count.py'):
            self.assertIn('tools/local_experiment/'+path,sources)
        with self.assertRaises(R.AssistRunError):R.sources(count=True,filtered=True)

    def test_cli_mode_is_explicit_and_mutually_exclusive_before_allocation(self):
        for args in (['--count-request-stdin','--filtered-request-stdin'],['--count','--request-stdin']):
            with patch.object(R,'COUNT_STATE',self.base/'no-allocation'),patch.object(R.subprocess,'run') as build, \
                    patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(R.main(args,stdin=io.BytesIO(json.dumps(INPUT).encode())),1)
            build.assert_not_called();self.assertIsNone(json.loads(out.getvalue())['evidence'])
        state=self.base/'count-state'
        value={'result':'passed','model_calls':1,'describe_cleanup_confirmed':True,'count_cleanup_confirmed':True}
        with patch.object(R,'COUNT_STATE',state),patch.object(R.subprocess,'run'), \
                patch.object(R,'execute',return_value=value) as execute,patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(R.main(['--count-request-stdin'],stdin=io.BytesIO(json.dumps(INPUT).encode())),0)
        self.assertEqual(execute.call_args.kwargs,{'count':True})
        output=next(state.iterdir());owner=json.loads((output/'owner-result.json').read_bytes())
        self.assertEqual(owner['profile'],K.PROFILE);self.assertEqual(owner['schema_version'],3)
        self.assertTrue(json.loads(out.getvalue())['reader_cleanup_confirmed'])


class CountAuditTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.output=Path(temp.name).resolve();self.output.chmod(0o700);self.saved=[]
        self.house,self.loki=FakeHouse(self.saved),FakeLoki()
        self.audit=AU.CountNotebookAudit('a'*32,self.output,{'provider':'local','model':'qwen'},
            lambda value:self.saved.append(deepcopy(value)),agenthouse=self.house,loki=self.loki)
        def close():
            if not self.audit.closed:
                try:self.audit.close(RuntimeError('test cleanup'))
                except BaseException:pass
        self.addCleanup(close)

    def test_count_has_four_correlated_actions_and_no_literal_in_audit(self):
        self.audit.start();self.audit.phase(K.DESCRIBE,lambda:{'private':'schema'})
        self.audit.phase(K.INFERENCE,lambda:{'prompt_tokens':42,'completion_tokens':9,'duration_ms':12},
            inference=True,validate=lambda value:{'where':{'column':'id','literal':'PRIVATE_LITERAL'}})
        self.audit.phase(K.COUNT,lambda:{'count':'2'})
        self.audit.finish();self.audit.close()
        self.assertTrue(self.audit.report['verified'] and self.audit.report['saved_spans_verified'])
        self.assertEqual([event['action_type'] for event in self.house.events],
                         [K.DESCRIBE,K.INFERENCE,K.COUNT,K.ASSESS])
        self.assertEqual([event['target_system'] for event in self.house.events],
                         ['trino-read-only','llm-local-compatible','trino-read-only','notebook-count-local'])
        self.assertEqual(self.house.events[1]['prompt_tokens'],42)
        self.assertNotIn('PRIVATE_LITERAL',json.dumps(self.saved)+json.dumps(self.house.events))

    def test_rejected_proposal_retains_known_usage_without_count_action(self):
        self.audit.start();self.audit.phase(K.DESCRIBE,lambda:None)
        def invalid(value):raise C.AssistError('NOTEBOOK_ASSIST_PROPOSAL_INVALID')
        error=None
        try:self.audit.phase(K.INFERENCE,lambda:{'prompt_tokens':91,'completion_tokens':17,'duration_ms':999},
                            inference=True,validate=invalid)
        except C.AssistError as caught:error=caught
        self.audit.close(error)
        self.assertEqual(self.house.events[1]['prompt_tokens'],91)
        self.assertEqual(self.house.events[1]['completion_tokens'],17)
        self.assertNotIn(K.COUNT,[event['action_type'] for event in self.house.events])

    def test_explicit_unsupported_closes_three_actions_without_count(self):
        self.audit.start();self.audit.phase(K.DESCRIBE,lambda:None)
        self.audit.phase(K.INFERENCE,lambda:{'prompt_tokens':10,'completion_tokens':5,'duration_ms':12},
                         inference=True,validate=lambda value:None)
        self.audit.finish(status='blocked');self.audit.close()
        self.assertEqual([event['action_type'] for event in self.house.events],[K.DESCRIBE,K.INFERENCE,K.ASSESS])
        self.assertEqual([event['target_system'] for event in self.house.events],
                         ['trino-read-only','llm-local-compatible','notebook-count-local'])
        self.assertTrue(self.audit.report['verified']);self.assertEqual(self.house.events[-1]['status'],'blocked')


if __name__=='__main__':unittest.main()
