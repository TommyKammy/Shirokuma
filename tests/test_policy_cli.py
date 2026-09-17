"""Policy product bridge using actual workflow evidence and fake external boundaries."""
import asyncio
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/local_experiment/bears'))
import policy_cli as CLI
import run_policy_candidate as R
from test_policy_candidate_bear import CandidateRuntimeFixture
from test_policy_candidate_observer import TOKEN,PRIVATE,pod
from test_local_sre_llm import local_config


class PolicyCLITests(CandidateRuntimeFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root=Path(self.temp.name).resolve()
        self.state=self.root/'policy-cli';self.evidence=self.root/'policy-candidate-bear'
        self.output=self.evidence/TOKEN
        for name,value in [('STATE',self.state),('EVIDENCE',self.evidence)]:
            item=patch.object(CLI,name,value);item.start();self.addCleanup(item.stop)
        async def run(config,output,raw):return await R.run(config,output,raw)
        self.runner=SimpleNamespace(run=Mock(side_effect=run))
        item=patch.object(CLI,'configured_runner',return_value=(self.runner,local_config()))
        self.configured=item.start();self.addCleanup(item.stop)

    def main(self,action='check',*,raw=None,args=None):
        out=io.StringIO();code=CLI.main(args if args is not None else [action,'--evidence-token',TOKEN],
            stdout=out,stdin=io.BytesIO(self.raw if raw is None else raw))
        rows=[json.loads(line) for line in out.getvalue().splitlines()]
        self.assertIn(len(rows),(1,2));self.assertEqual(set(rows[-1]),CLI.FINAL_KEYS)
        for value in (PRIVATE,'private-pod','private-container',str(self.root)):
            self.assertNotIn(value,out.getvalue())
        return code,rows

    def files(self):
        return {str(p.relative_to(self.root)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode,p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def raw_documents(self):
        return {p.name:p.read_bytes() for p in self.output.iterdir()}

    def test_actual_allow_one_invocation_distinct_workflow_and_replay_refusal(self):
        code,rows=self.main();self.assertEqual(code,0)
        final=rows[-1];self.assertEqual(final['result'],'passed');self.assertEqual(final['admission'],'allowed')
        self.assertEqual(final['evidence_token'],TOKEN);self.assertNotEqual(final['workflow_token'],TOKEN)
        self.assertFalse(final['authority_granted']);self.assertEqual(final['persisted_workloads'],0)
        self.assertEqual(final['expected_violation_ids'],[]);self.assertEqual(final['reported_violation_ids'],[])
        before=self.files();code,rows=self.main();self.assertEqual(code,1)
        self.assertEqual(rows[-1]['error_code'],'POLICY_ALREADY_EXISTS');self.assertEqual(rows[-1]['execution'],'not-started')
        self.assertEqual(self.runner.run.call_count,1);self.assertEqual(self.files(),before)

    def test_verified_denial_has_exit_two_but_status_inspection_exit_zero(self):
        self.raw=json.dumps(pod(privileged=True)).encode()
        self.kube.respond=lambda body:self.kube.denial(body,['P01'])
        code,rows=self.main();self.assertEqual(code,2);self.assertEqual(rows[-1]['result'],'passed')
        self.assertEqual(rows[-1]['admission'],'denied');self.assertEqual(rows[-1]['expected_violation_ids'],['P01'])
        self.assertEqual(self.main('status')[0],0)

    def test_invalid_stdin_precedes_configuration_and_durable_intent(self):
        for raw in (b'',b'{}',b'[]',b'{"secret":"private"}',b'x'*(16384+1),b'\xff'):
            code,rows=self.main(raw=raw);self.assertEqual(code,1)
            self.assertEqual(rows[-1]['error_code'],'INPUT_INVALID');self.assertEqual(rows[-1]['result'],'not_started')
        self.configured.assert_not_called();self.runner.run.assert_not_called();self.assertFalse(self.state.exists())

    def test_invalid_arguments_no_input_or_arbitrary_paths_never_invoke(self):
        for args in ([],['check'],['resume','--evidence-token',TOKEN],['status','--evidence-token','../escape'],
                     ['check','--evidence-token',TOKEN,'--request',PRIVATE]):
            self.assertEqual(self.main(args=args)[0],1)
        self.configured.assert_not_called();self.runner.run.assert_not_called()

    def test_durable_intent_and_lock_precede_actual_workflow(self):
        async def run(config,output,raw):
            journal=CLI.loads(CLI.read(self.state/TOKEN/'intent.json'))
            self.assertEqual(journal['invocations'],1);self.assertEqual(journal['state'],'invoked')
            with CLI.lock(self.state/TOKEN/'run.lock') as busy:self.assertTrue(busy)
            return await R.run(config,output,raw)
        self.runner.run.side_effect=run
        self.assertEqual(self.main()[0],0)

    def test_fsync_and_intent_failures_do_not_invoke_workflow(self):
        for name in ('sync','persist'):
            with self.subTest(name=name),patch.object(CLI,name,side_effect=OSError('private')):
                code,rows=self.main();self.assertEqual(code,1);self.assertEqual(rows[-1]['execution'],'not-started')
        self.runner.run.assert_not_called();self.assertEqual(self.kube.requests,[])

    def test_environment_failure_and_preintent_interrupt_are_not_started(self):
        for error,code in ((ImportError('private'),1),(asyncio.CancelledError(),130)):
            self.configured.side_effect=error
            observed,rows=self.main();self.assertEqual(observed,code);self.assertEqual(rows[-1]['result'],'not_started')
        self.runner.run.assert_not_called()

    def test_actual_model_disagreement_is_closed_failed_not_admission_denied(self):
        self.mismatch=True
        code,rows=self.main();self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'failed')
        self.assertEqual(rows[-1]['admission'],'not-observed');self.assertEqual(self.kube.requests,[])
        before=self.files();self.assertEqual(self.main('status')[0],0);self.assertEqual(self.files(),before)

    def test_actual_api_timeout_is_unknown_admission_without_retry(self):
        def fail(body):raise TimeoutError('private')
        self.kube.respond=fail
        code,rows=self.main();self.assertEqual(code,1);self.assertEqual(rows[-1]['admission'],'unknown')
        self.assertNotEqual(rows[-1]['result'],'passed');self.assertEqual(len(self.kube.requests),1)
        self.assertEqual(self.main('status')[0],0);self.assertEqual(len(self.kube.requests),1)

    def test_workflow_stdout_and_stderr_and_exception_are_never_forwarded(self):
        async def fail(*args):
            print(PRIVATE);print(PRIVATE,file=sys.stderr);raise RuntimeError(PRIVATE)
        self.runner.run.side_effect=fail
        code,rows=self.main();self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'unknown')
        self.assertEqual(self.main('status')[1][-1]['verification'],'journal-only')

    def test_cancel_original_survives_final_journal_failure(self):
        original=asyncio.CancelledError()
        async def cancel(*args):raise original
        self.runner.run.side_effect=cancel;persist=CLI.persist
        def write(directory,journal):
            if journal['state']=='interrupted':raise OSError('secondary')
            return persist(directory,journal)
        with patch.object(CLI,'persist',side_effect=write):
            with self.assertRaises(asyncio.CancelledError) as caught:CLI.invoke(TOKEN,{},self.raw)
        self.assertIs(caught.exception,original);self.assertEqual(self.runner.run.call_count,1)

    def test_actual_model_cancel_has_interrupt_exit_and_no_dryrun(self):
        self.model_error=asyncio.CancelledError()
        code,rows=self.main();self.assertEqual(code,130);self.assertEqual(rows[-1]['error_code'],'INTERRUPTED')
        self.assertEqual(rows[-1]['result'],'unknown');self.assertEqual(self.kube.requests,[])
        journal=CLI.loads(CLI.read(self.state/TOKEN/'intent.json'));self.assertEqual(journal['state'],'interrupted')

    def test_status_loads_no_configuration_and_preserves_all_files(self):
        self.assertEqual(self.main()[0],0);before=self.files();reads=copy.deepcopy(self.kube.reads)
        events=copy.deepcopy(self.client.events);model_calls=copy.deepcopy(self.calls)
        self.configured.side_effect=AssertionError('no live configuration')
        with patch.object(CLI,'runner',side_effect=AssertionError('no live runner')):
            for _ in range(2):self.assertEqual(self.main('status')[0],0)
        self.assertEqual(self.files(),before);self.assertEqual(self.kube.reads,reads)
        self.assertEqual(self.client.events,events);self.assertEqual(self.calls,model_calls)

    def test_missing_status_is_read_only_and_creates_no_directory(self):
        code,rows=self.main('status');self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'not_found')
        self.assertFalse(self.state.exists());self.assertFalse(self.evidence.exists());self.configured.assert_not_called()

    def test_busy_status_does_not_read_changing_evidence(self):
        self.main();before=self.files()
        with (self.state/TOKEN/'run.lock').open('rb') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(CLI,'verify_documents',side_effect=AssertionError('no active evidence')):
                code,rows=self.main('status');self.assertEqual(code,0);self.assertEqual(rows[-1]['result'],'busy')
            fcntl.flock(stream,fcntl.LOCK_UN)
        self.assertEqual(self.files(),before)

    def test_saved_file_hash_tamper_is_unknown_and_noop(self):
        self.main();path=self.output/'candidate.json';data=json.loads(path.read_text());data['body_sha256']='f'*64
        path.write_text(json.dumps(data));before=self.files()
        code,rows=self.main('status');self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'unknown')
        self.assertEqual(self.files(),before)

    def test_unsealed_saved_semantic_tampering_is_rejected(self):
        self.main();original=self.raw_documents()
        edits=[('candidate.json',lambda d:d.update(body_sha256='f'*64)),
               ('observation.json',lambda d:d['baseline'].update(namespace_uid='00000000-0000-0000-0000-000000000999')),
               ('observation.json',lambda d:d['policy_sources'].update(native_manifest='f'*64)),
               ('classification.json',lambda d:d['plan']['classifications'][0].update(violation_ids=['P01'])),
               ('verification.json',lambda d:d.update(reported_violation_ids=['P01'])),
               ('result.json',lambda d:d.update(task_id='workflow-'+'f'*32)),
               ('result.json',lambda d:d.update(persisted_workloads=1))]
        for name,edit in edits:
            with self.subTest(name=name):
                raw=copy.deepcopy(original);value=json.loads(raw[name]);edit(value);raw[name]=json.dumps(value).encode()
                with self.assertRaises((ValueError,KeyError,TypeError)):CLI.verify_documents(raw)

    def test_saved_span_ids_counts_and_closed_root_are_verified(self):
        self.main();original=self.raw_documents()
        spans=[json.loads(line) for line in original['spans.jsonl'].splitlines()]
        self.assertEqual(len(spans),4)
        for edit in (lambda s:s[-1].update(trace_id='f'*32),lambda s:s.append(copy.deepcopy(s[0])),
                     lambda s:s[-1].update(end_time_unix_nano=None)):
            raw=copy.deepcopy(original);changed=copy.deepcopy(spans);edit(changed)
            raw['spans.jsonl']='\n'.join(json.dumps(s) for s in changed).encode()
            with self.assertRaises((ValueError,KeyError,TypeError)):CLI.verify_documents(raw)

    def test_symlink_and_hardlinked_receipts_or_lock_are_rejected(self):
        self.main();target=self.output/'candidate.json';saved=target.read_bytes()
        outside=self.root/'outside';outside.write_bytes(saved);outside.chmod(0o600)
        target.unlink();target.symlink_to(outside)
        self.assertEqual(self.main('status')[0],1)
        target.unlink();os.link(outside,target);self.assertEqual(self.main('status')[0],1)
        target.unlink();target.write_bytes(saved);target.chmod(0o600)
        lock=self.state/TOKEN/'run.lock';lock.unlink();lock.symlink_to(outside)
        self.assertEqual(self.main('status')[0],1)

    def test_unknown_extra_artifact_is_not_ignored(self):
        self.main();(self.output/'raw-manifest.json').write_bytes(self.raw)
        code,rows=self.main('status');self.assertEqual(code,1);self.assertEqual(rows[-1]['result'],'unknown')

    def test_initial_running_receipt_is_not_terminal_failure_or_success(self):
        initial={};original=R.persist
        def capture(path,value):
            if path.name=='result.json' and not initial:
                initial.update(copy.deepcopy(value))
            original(path,value)
        with patch.object(R,'persist',side_effect=capture):self.main()
        documents={'candidate.json':(self.output/'candidate.json').read_bytes(),
            'result.json':json.dumps(initial).encode(),'spans.jsonl':b''}
        view=CLI.verify_documents(documents)
        self.assertEqual(view['result'],'unknown');self.assertEqual(view['verification'],'journal-only')


if __name__=='__main__':unittest.main()
