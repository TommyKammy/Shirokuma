"""Bounded SRE bridge, saved evidence and interruption tests; no live services."""
import asyncio
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch, AsyncMock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/local_experiment/bears'))
import sre_cli as CLI

TOKEN, INNER, TRACE = '1'*32, '2'*32, '3'*32
PRIVATE = 'PRIVATE_GATEWAY_SECRET_PROMPT_PATH'


def fixture_documents(*, legacy=False, three=False):
    C = CLI.C
    stamp = '2026-09-17T06:00:00.000000Z'
    initial = C.project_observations({
        'shirokuma_doctor': dict(profile='local-experiment', context='colima-mac-studio-solo', observed_at=stamp,
            checks=[dict(name='storage',status='healthy')]),
        'k8s_read_pods': dict(namespace='shirokuma-dev',observed_at=stamp,truncated=False,
            pods=[dict(name='trino',uid='pod-1',phase='Running',ready_containers=0,total_containers=1,restarts=0)]),
        'k8s_read_events': dict(namespace='shirokuma-dev',observed_at=stamp,truncated=False,events=[]),
        'local_platform_status':dict(profile='local-experiment',observed_at=stamp,ready=True,
            components={name:dict(ready=True,status='ready') for name in C.COMPONENTS})})
    if three: initial.pop('local_platform_status')
    evidence = C.build_evidence(*(initial[t] for t in C.TOOLS[:3]),initial.get('local_platform_status'))
    if three:
        evidence['observations'].pop('platform')
        evidence['findings'] = [f for f in evidence['findings'] if f['subject'] != 'platform']
        for i,f in enumerate(evidence['findings'],1): f['id'] = f'f{i:02}'
    if legacy or three: evidence['observations']['pods'].pop('container_details')
    triage = dict(assessment='attention-required',root_cause='undetermined',
        checks=[dict(tool='k8s_read_pods',finding_ids=[f['id'] for f in evidence['findings']])])
    followup = {'k8s_read_pods':copy.deepcopy(initial['k8s_read_pods'])}
    followup['k8s_read_pods']['observed_at'] = '2026-09-17T06:00:01.000000Z'
    comparisons = CLI.compatible_comparison(initial,followup,old_three=three,legacy_details=legacy or three)
    observations = [dict(tool=t,phase='initial',snapshot=s) for t,s in initial.items()]
    observations += [dict(tool=t,phase='followup',snapshot=s) for t,s in followup.items()]
    calls = [dict(tool=o['tool'],phase=o['phase'],event_id=str(uuid.UUID(int=i)),span_id=f'{i:016x}',
                  action_type='mcp.'+o['tool']) for i,o in enumerate(observations,1)]
    metrics = dict(prompt_tokens=10,completion_tokens=20,inference_cost_estimate=None,duration_ms=3)
    records,spans = [],[]
    for i,action in enumerate(('llm.sre_triage','bear.sre.assess'),10):
        records.append(dict(event_id=str(uuid.UUID(int=i)),span_id=f'{i:016x}',task_id='workflow-'+INNER,
            trace_id=TRACE,action_type=action,status='success',audit_delivery='delivered',
            model_provider='local' if i==10 else 'none',model_name='fixed' if i==10 else '',
            **(metrics if i==10 else dict(prompt_tokens=None,completion_tokens=None,inference_cost_estimate=None,duration_ms=10))))
        spans.append(dict(name=action,trace_id=TRACE,span_id=f'{i:016x}',status='OK',
            start_time_unix_nano=2 if i==10 else 1,end_time_unix_nano=10 if i==10 else 100,
            attributes={} if i==10 else {'task_id':'workflow-'+INNER}))
    result = dict(result='passed',scope='local-read-only-sre-followup',task_id='workflow-'+INNER,trace_id=TRACE,
        provider='local',model='fixed',remediation_executed=False,observations_file='observations.json',
        tool_calls=calls,audit_records=records,evidence=evidence,triage=triage,inference=metrics,comparisons=comparisons,
        initial_sha256=CLI.source_hash(initial),followup_sha256={t:CLI.source_hash(s) for t,s in followup.items()},
        checkpoint='completed-in-memory',correlated_events=len(calls)+2)
    return {'result.json':result,'observations.json':observations,'spans.jsonl':spans,'gateway-stderr.log':PRIVATE.encode()}


def encode(documents):
    return {name: value if type(value) is bytes else
        ('\n'.join(CLI.canonical(v) for v in value) if name.endswith('jsonl') else CLI.canonical(value)).encode()
        for name,value in documents.items()}


def write_fixture(output,documents=None):
    documents = fixture_documents() if documents is None else documents
    output.mkdir(mode=0o700)
    for name,raw in encode(documents).items():
        p=output/name;p.write_bytes(raw);p.chmod(0o600)
    return documents['result.json']


class SRECLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.state=self.root/'sre-cli';self.evidence=self.root/'sre-llm'
        for name,value in [('STATE',self.state),('EVIDENCE',self.evidence)]:
            p=patch.object(CLI,name,value);p.start();self.addCleanup(p.stop)
        async def run(config,output):return write_fixture(output)
        self.fake=SimpleNamespace(run=Mock(side_effect=run))
        p=patch.object(CLI,'configured_runner',return_value=(self.fake,{}));self.configured=p.start();self.addCleanup(p.stop)

    def main(self,action='run',args=None):
        out=io.StringIO();code=CLI.main(args if args is not None else [action,'--evidence-token',TOKEN],stdout=out)
        rows=[json.loads(line) for line in out.getvalue().splitlines()]
        self.assertIn(len(rows),(1,2));self.assertEqual(set(rows[-1]),CLI.FINAL_KEYS)
        self.assertNotIn(PRIVATE,out.getvalue());self.assertNotIn(str(self.root),out.getvalue())
        return code,rows

    def files(self):
        return {str(p.relative_to(self.root)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode,p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def legacy(self,docs):
        self.evidence.mkdir(mode=0o755);write_fixture(self.evidence/TOKEN,docs)

    def test_run_once_has_distinct_tokens_and_rejects_reinvocation(self):
        code,rows=self.main();self.assertEqual(code,0)
        self.assertEqual(rows[-1]['workflow_token'],INNER);self.assertEqual(rows[-1]['evidence_token'],TOKEN)
        self.assertEqual(rows[-1]['assessment'],'attention-required');self.assertFalse(rows[-1]['remediation_executed'])
        before=self.files();code,rows=self.main()
        self.assertEqual(code,1);self.assertEqual(rows[-1]['error_code'],'SRE_ALREADY_EXISTS')
        self.assertEqual(rows[-1]['execution'],'not-started');self.assertEqual(self.fake.run.call_count,1)
        self.assertEqual(self.files(),before)

    def test_no_input_arguments_or_unsafe_identity_rejected_without_config(self):
        for args in ([],['run'],['run','--evidence-token',TOKEN,'--case','local-lab'],
                     ['run','--evidence-token',TOKEN,'--request',PRIVATE],['status','--evidence-token','../wrong'],
                     ['resume','--evidence-token',TOKEN]):
            self.assertEqual(self.main(args=args)[0],1)
        self.configured.assert_not_called();self.fake.run.assert_not_called()

    def test_durable_intent_precedes_only_invocation(self):
        async def run(config,output):
            j=CLI.loads(CLI.read(self.state/TOKEN/'intent.json'))
            self.assertEqual(j['state'],'invoked');self.assertEqual(j['invocations'],1)
            self.assertEqual(j['case'],'local-lab')
            with CLI.lock(self.state/TOKEN/'run.lock') as busy:self.assertTrue(busy)
            return write_fixture(output)
        self.fake.run.side_effect=run;self.assertEqual(self.main()[0],0)

    def test_intent_write_failure_prevents_workflow(self):
        with patch.object(CLI,'persist',side_effect=OSError(PRIVATE)):
            code,rows=self.main()
        self.assertEqual(code,1);self.assertEqual(rows[-1]['execution'],'not-started');self.fake.run.assert_not_called()

    def test_environment_failure_and_preintent_interrupt_are_not_started(self):
        for error,expected in [(ImportError(PRIVATE),1),(asyncio.CancelledError(),130)]:
            self.configured.side_effect=error
            code,rows=self.main();self.assertEqual(code,expected);self.assertEqual(rows[-1]['result'],'not_started')
        self.fake.run.assert_not_called()

    def test_library_output_and_unknown_error_are_suppressed(self):
        async def run(*args):
            print(PRIVATE);print(PRIVATE,file=sys.stderr);raise RuntimeError(PRIVATE)
        self.fake.run.side_effect=run;code,rows=self.main()
        self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'unknown')

    def test_cancel_survives_failed_final_journal_save(self):
        async def run(*args):raise asyncio.CancelledError()
        self.fake.run.side_effect=run;original=CLI.persist
        def save(directory,journal):
            if journal['state']=='interrupted':raise OSError(PRIVATE)
            return original(directory,journal)
        with patch.object(CLI,'persist',side_effect=save):code,rows=self.main()
        self.assertEqual(code,130);self.assertEqual(rows[-1]['error_code'],'INTERRUPTED')

    def test_status_is_readonly_and_never_loads_live_config(self):
        self.main();before=self.files();self.configured.side_effect=AssertionError('live status')
        for _ in range(2):
            code,rows=self.main('status');self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'passed')
        self.assertEqual(self.files(),before);self.assertEqual(self.fake.run.call_count,1)

    def test_saved_hash_or_source_mutation_is_not_passed(self):
        self.main();p=self.evidence/TOKEN/'result.json';v=CLI.loads(p.read_bytes());v['model']='changed'
        p.write_text(CLI.canonical(v));before=self.files()
        code,rows=self.main('status');self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'unknown')
        self.assertEqual(self.files(),before)

    def test_busy_lock_does_not_read_changing_evidence(self):
        self.main();before=self.files()
        with (self.state/TOKEN/'run.lock').open('rb') as f:
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(CLI,'inspect_saved',side_effect=AssertionError('busy deep read')):code,rows=self.main('status')
        self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'busy');self.assertEqual(self.files(),before)

    def test_legacy_three_sources_have_no_fabricated_platform_or_container(self):
        self.legacy(fixture_documents(three=True));before=self.files()
        code,rows=self.main('status');self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'passed')
        self.assertEqual(self.files(),before);self.assertFalse(self.state.exists())

    def test_legacy_four_sources_keep_absent_container_details(self):
        self.legacy(fixture_documents(legacy=True));before=self.files()
        code,rows=self.main('status');self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'passed')
        self.assertEqual(self.files(),before)

    def test_legacy_missing_snapshots_stays_unknown(self):
        docs=fixture_documents();docs.pop('observations.json');self.legacy(docs)
        code,rows=self.main('status');self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'unknown')
        self.assertIsNone(rows[-1]['assessment']);self.assertEqual(rows[-1]['verification'],'journal-only')

    def test_initial_failed_without_closed_root_is_not_terminal(self):
        docs=fixture_documents();r=docs['result.json'];r['result']='failed';r.pop('checkpoint')
        r['audit_records']=r['audit_records'][:1];docs['spans.jsonl']=docs['spans.jsonl'][:1]
        self.legacy(docs);code,rows=self.main('status')
        self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'unknown')

    def test_closed_failed_root_reports_failed_inspection_exit_zero(self):
        docs=fixture_documents();r=docs['result.json'];r.update(result='failed',error_code='SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED')
        r.pop('checkpoint');r['audit_records'][-1]['status']='failed';docs['spans.jsonl'][-1]['status']='ERROR'
        self.legacy(docs);code,rows=self.main('status')
        self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'failed')

    def test_tampered_reconstruction_triage_comparison_or_hash_rejects(self):
        edits=[lambda d:d['result.json']['triage']['checks'].clear(),
               lambda d:d['result.json']['evidence'].update(health='healthy-observed-scope'),
               lambda d:d['result.json']['comparisons'].update(root_cause='recovered'),
               lambda d:d['result.json'].update(initial_sha256='0'*64),
               lambda d:d['observations.json'][1]['snapshot']['pods'][0].update(uid='other'),
               lambda d:d['observations.json'][0]['snapshot'].update(raw=PRIVATE)]
        for edit in edits:
            docs=fixture_documents();edit(docs)
            with self.assertRaises((ValueError,KeyError)):CLI.verify_documents(encode(docs))

    def test_mcp_span_correlation_is_not_mistaken_for_saved_local_spans(self):
        docs=fixture_documents();self.assertEqual(len(docs['spans.jsonl']),2)
        self.assertEqual(len(docs['result.json']['tool_calls']),5)
        self.assertEqual(CLI.verify_documents(encode(docs))['result'],'passed')
        docs['spans.jsonl'].append(dict(docs['spans.jsonl'][0],span_id=docs['result.json']['tool_calls'][0]['span_id']))
        with self.assertRaises(ValueError):CLI.verify_documents(encode(docs))

    def test_duplicate_mcp_identity_and_unclosed_root_reject(self):
        for edit in [lambda d:d['result.json']['tool_calls'][0].update(span_id=d['result.json']['audit_records'][0]['span_id']),
                     lambda d:d['spans.jsonl'][-1].update(end_time_unix_nano=None),
                     lambda d:d['result.json']['tool_calls'][0].update(action_type='mcp.other')]:
            docs=fixture_documents();edit(docs)
            with self.assertRaises(ValueError):CLI.verify_documents(encode(docs))

    def test_private_file_symlink_and_stderr_tamper_boundaries(self):
        code,rows=self.main('status');self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'not_found')
        self.main();p=self.evidence/TOKEN/'gateway-stderr.log';p.write_bytes(b'changed')
        self.assertEqual(self.main('status')[0],1)
        p.unlink();p.symlink_to(self.root/'missing');self.assertEqual(self.main('status')[0],1)

    def test_exact_safe_shape_unknown_assessment_can_still_complete(self):
        value=CLI.projection('run',TOKEN,result='passed',execution='invoked',verification='saved-evidence',
                            workflow_token=INNER,assessment='unknown',has_evidence=True)
        for update in ({'remediation_executed':None},{'remediation_executed':True},{'assessment':None},
                       {'root_cause':'OOM'},{'workflow_token':None},{'raw_log':PRIVATE}):
            with self.assertRaises(ValueError):CLI.validate_projection(dict(value,**update))

    def test_isolated_status_needs_no_runner_or_optional_service_package(self):
        p=subprocess.run([sys.executable,'-I','-B',str(Path(CLI.__file__)),'status','--evidence-token','0'*32],
            cwd=self.root,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,1);self.assertEqual(p.stderr,'')
        self.assertEqual(json.loads(p.stdout.splitlines()[-1])['result'],'not_found')


@unittest.skipUnless(importlib.util.find_spec('langgraph'),'requires pinned LLM environment')
class CancellationTests(unittest.TestCase):
    def test_actual_workflow_fake_services_produce_verifiable_closed_local_spans(self):
        import run_sre_llm as R
        import mcp
        from test_local_sre_llm import local_config, snapshots, FixtureSelector, EvaluationClient, EVALUATION
        agenthouse=EVALUATION.agenthouse_module();logs=[];binding={};lifecycle=[]
        class Client(EvaluationClient):
            def query(self,sql):
                if ' WHERE event_id IN (' in sql:
                    return json.dumps({'data':self.events})
                return super().query(sql)
        client=Client();model=FixtureSelector()
        class Gateway:
            async def __aenter__(self):lifecycle.append('enter');return self
            async def __aexit__(self,*args):lifecycle.append('exit')
            async def call_tool(self,tool,args,**kwargs):
                index=len(client.events)+1;span=f'{index:016x}'
                event=agenthouse.event('mcp.'+tool,'kubernetes',agent_id='sre-fixture',
                    task_id=binding['task'],trace_id=binding['trace'],status='success',blocked_by_policy=False,
                    model_provider='none',model_name='',prompt_tokens=None,completion_tokens=None,
                    inference_cost_estimate=None,duration_ms=1)
                client.events.append(event)
                logs.append(dict(task_id=binding['task'],trace_id=binding['trace'],span_id=span,
                                 action='mcp.'+tool,status='success'))
                data=snapshots()[tool]
                if index>4:data['observed_at']='2026-09-17T02:00:01.000000Z'
                return SimpleNamespace(is_error=False,structured_content=dict(status='success',audit='delivered',
                    task_id=binding['task'],trace_id=binding['trace'],event_id=event['event_id'],span_id=span,data=data))
        def environment(task,traceparent):
            binding.update(task=task,trace=traceparent.split('-')[1]);return {}
        loki=SimpleNamespace(push_record=logs.append,query_task=lambda task:logs)
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'evidence'
            with patch.object(R,'SREProvider',return_value=model),patch.object(R,'child_environment',side_effect=environment),\
                 patch.object(R,'module',side_effect=lambda name,path:agenthouse if 'agenthouse' in name else loki),\
                 patch.object(agenthouse,'Client',return_value=client),patch.object(mcp,'stdio_client',return_value=None),\
                 patch.object(mcp,'Client',return_value=Gateway()):
                result=asyncio.run(R.run(local_config(),output))
            self.assertEqual(result['result'],'passed');self.assertEqual(lifecycle,['enter','exit'])
            self.assertEqual(len(model.calls),1)
            report=CLI.verify_documents({file.name:file.read_bytes() for file in output.iterdir()})
            self.assertEqual(report['result'],'passed');self.assertEqual(report['assessment'],'attention-required')
            self.assertEqual(len(result['tool_calls']),6)
            self.assertEqual(len((output/'spans.jsonl').read_text().splitlines()),2)

    def test_model_cancel_retains_original_when_audit_fails(self):
        import run_sre_llm as R
        from test_local_sre_llm import snapshots,FakeTracer
        import threading
        async def run():
            started,release=threading.Event(),threading.Event()
            def complete(*args):started.set();release.wait(10);return {}
            graph=R.build_graph(SimpleNamespace(complete=complete),AsyncMock(side_effect=lambda t,p:snapshots()[t]),
                                Mock(side_effect=OSError(PRIVATE)),FakeTracer())
            task=asyncio.create_task(graph.ainvoke({}, {'configurable':{'thread_id':'cancel'}}))
            try:
                while not started.is_set():await asyncio.sleep(.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            finally:release.set()
        asyncio.run(run())

    def test_outer_cancel_keeps_mcp_context_exit_error_span_and_original_save_failure(self):
        self.check_outer_cancel(save_failure=True)

    def test_outer_cancel_survives_mcp_exit_secondary_error(self):
        self.check_outer_cancel(exit_behavior='error')

    def test_outer_cancel_survives_mcp_exit_suppression(self):
        self.check_outer_cancel(exit_behavior='suppress')

    def check_outer_cancel(self, *, save_failure=False, exit_behavior='normal'):
        import run_sre_llm as R
        import mcp
        from test_local_sre_llm import local_config,EvaluationClient,EVALUATION
        error=asyncio.CancelledError();client=EvaluationClient();agenthouse=EVALUATION.agenthouse_module();lifecycle=[]
        class Gateway:
            async def __aenter__(self):lifecycle.append('enter');return self
            async def __aexit__(self,*args):
                lifecycle.append('exit')
                if exit_behavior=='error':raise OSError(PRIVATE)
                return exit_behavior=='suppress'
        class Graph:
            async def ainvoke(self,*args):raise error
        original=R.private_write
        def write(path,value):
            if save_failure and path.name=='result.json' and value.get('result')=='interrupted':raise OSError(PRIVATE)
            return original(path,value)
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'evidence'
            with patch.object(R,'SREProvider'),patch.object(R,'build_graph',return_value=Graph()),\
                 patch.object(R,'module',side_effect=lambda name,path:agenthouse if 'agenthouse' in name else Mock()),\
                 patch.object(agenthouse,'Client',return_value=client),patch.object(mcp,'stdio_client',return_value=None),\
                 patch.object(mcp,'Client',return_value=Gateway()),patch.object(R,'private_write',side_effect=write):
                with self.assertRaises(asyncio.CancelledError) as caught:asyncio.run(R.run(local_config(),output))
            self.assertIs(caught.exception,error);self.assertEqual(lifecycle,['enter','exit'])
            self.assertEqual((output/'gateway-stderr.log').stat().st_mode&0o777,0o600)
            spans=[json.loads(s) for s in (output/'spans.jsonl').read_text().splitlines()]
            self.assertEqual(spans[-1]['name'],'bear.sre.assess');self.assertEqual(spans[-1]['status'],'ERROR')


if __name__=='__main__':unittest.main()
