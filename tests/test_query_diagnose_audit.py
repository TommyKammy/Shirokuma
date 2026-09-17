"""Real local OTel export/correlation with fake AgentHouse and Loki I/O."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from tools.local_experiment.query_diagnose import audit as A, run as R, contract as D
from test_query_diagnose_run import FakeLease, observation, TOKEN
from test_query_diagnose_contract import source


class Client:
    def __init__(self): self.rows = []
    def query(self, sql):
        if sql == 'SELECT 1': return '1'
        ids = re.findall(r"'([0-9a-f-]{36})'", sql)
        return json.dumps({'data': [row for row in self.rows if row['event_id'] in ids]})


class AuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.output = Path(tmp.name).resolve(); self.output.chmod(0o700)
        self.client, self.logs, self.saved = Client(), [], []
        def event(action, tool, **kwargs):
            return dict(event_id=str(uuid.uuid4()), action_type=action, target_system=tool, **kwargs)
        self.ah = SimpleNamespace(Client=lambda:self.client, event=event,
                                  insert=lambda client, event:client.rows.append(deepcopy(event)))
        self.loki = SimpleNamespace(push_record=lambda row:self.logs.append(deepcopy(row)),
                                    query_task=lambda task:[r for r in self.logs if r['task_id']==task])
        self.profile = {'provider':'local-compatible','model':'qwen3.8-flash-next'}

    def create(self, progress=None):
        self.audit = A.Audit(TOKEN, self.output, self.profile, progress or (lambda v:self.saved.append(deepcopy(v))),
                             agenthouse=self.ah, loki=self.loki)
        return self.audit

    def observe(self): return self.audit.phase('bear.query_diagnose.observe', lambda:{'safe':True})

    def model(self):
        return self.audit.phase('llm.query_diagnose', lambda:{'content':{'private-response-sentinel':'excluded'},
            'prompt_tokens':401, 'completion_tokens':31, 'duration_ms':123}, inference=True, validate=lambda _: {'accepted':True})

    def test_success_correlates_three_saved_spans_metrics_and_records(self):
        audit = self.create(); audit.start(); self.observe(); self.model(); audit.finish(); audit.close()
        self.assertTrue(audit.report['verified']); self.assertTrue(audit.report['saved_spans_verified'])
        self.assertEqual(len(self.client.rows),3); self.assertEqual(len(self.logs),3)
        rows = audit.report['records']; self.assertEqual(rows[1]['prompt_tokens'],401)
        self.assertEqual(rows[1]['completion_tokens'],31); self.assertEqual(rows[1]['duration_ms'],123)
        self.assertIsNone(rows[1]['inference_cost_estimate']); self.assertIsNone(rows[0]['prompt_tokens'])
        spans = [json.loads(line) for line in (self.output/'spans.jsonl').read_text().splitlines()]
        self.assertEqual(len(spans),3); self.assertTrue(all(s['status']=='OK' for s in spans))
        self.assertEqual({s['trace_id'] for s in spans},{audit.report['trace_id']})
        self.assertEqual((self.output/'spans.jsonl').stat().st_mode&0o777,0o600)
        self.assertNotIn('private-response-sentinel',json.dumps(self.saved)+(self.output/'spans.jsonl').read_text())

    def test_model_rejection_retains_known_usage_and_blocked_status(self):
        audit=self.create();audit.start();self.observe()
        def reject(_): raise R.DiagnoseError('MODEL_REJECTED')
        with self.assertRaises(R.DiagnoseError) as raised:
            audit.phase('llm.query_diagnose',lambda:{'content':{},'prompt_tokens':20,'completion_tokens':5},
                        inference=True,validate=reject)
        audit.close(raised.exception)
        self.assertEqual([r['status'] for r in audit.report['records']],['success','blocked','failed'])
        self.assertEqual(audit.report['records'][1]['prompt_tokens'],20)
        self.assertFalse(audit.report['verified'])

    def test_provider_failure_retains_actual_provider_error_metrics(self):
        from tools.local_experiment.query_diagnose.provider import ProviderError
        audit=self.create();audit.start();self.observe()
        failure=ProviderError('private upstream text',{'prompt_tokens':91,'completion_tokens':17,'duration_ms':999})
        def fail(): raise failure
        with self.assertRaises(ProviderError) as caught:
            audit.phase('llm.query_diagnose',fail,inference=True)
        self.assertIs(caught.exception,failure);audit.close(failure)
        record=audit.report['records'][1]
        self.assertEqual((record['prompt_tokens'],record['completion_tokens'],record['duration_ms']),(91,17,999))
        self.assertEqual(record['status'],'failed')
        self.assertNotIn('private upstream text',json.dumps(self.saved))

    def test_first_interruption_survives_failed_audit_write(self):
        audit=self.create();audit.start();self.observe(); signal=KeyboardInterrupt()
        def fail(): raise signal
        def broken(*_): raise OSError('PRIVATE WRITE ERROR')
        self.ah.insert=broken
        with self.assertRaises(KeyboardInterrupt) as raised:
            audit.phase('llm.query_diagnose',fail,inference=True)
        self.assertIs(raised.exception,signal)
        with self.assertRaises(A.AuditError):audit.close(signal)
        self.assertFalse(audit.report['verified'])
        self.assertNotIn('PRIVATE WRITE ERROR',json.dumps(self.saved))

    def test_span_exit_failure_cannot_replace_or_suppress_first_cancellation(self):
        signal = KeyboardInterrupt()
        class Context:
            def __enter__(self): return None
            def __exit__(self, *_): raise OSError('secondary span export')
        with self.assertRaises(KeyboardInterrupt) as caught:
            with A.preserve_context(Context()): raise signal
        self.assertIs(caught.exception, signal)
        class Suppresses(Context):
            def __exit__(self, *_): return True
        with self.assertRaises(KeyboardInterrupt) as caught:
            with A.preserve_context(Suppresses()): raise signal
        self.assertIs(caught.exception, signal)

    def test_missing_loki_or_usage_mismatch_cannot_verify(self):
        audit=self.create();audit.start();self.observe();self.model()
        self.logs.clear()
        with self.assertRaises(Exception) as raised:audit.finish()
        audit.close(raised.exception)
        self.assertFalse(audit.report['verified'])

    def test_saved_span_corruption_withholds_verified(self):
        audit=self.create();audit.start();self.observe();self.model();audit.finish()
        with (self.output/'spans.jsonl').open('a') as stream:stream.write('{}\n')
        with self.assertRaises(A.AuditError):audit.close()
        self.assertFalse(audit.report['verified'])

    def test_owner_with_actual_audit_and_fake_external_calls(self):
        def factory(token,output,profile,progress):return self.create(progress)
        model=SimpleNamespace(complete=lambda phase,evidence:{'content':D.expected_diagnosis(evidence),
            'prompt_tokens':123,'completion_tokens':22,'duration_ms':250})
        def observe(src,token,progress):
            value=observation();progress(value);return value
        owner=R.Owner(TOKEN,source()['read_token'],self.output)
        report,public=owner.execute(lease_factory=FakeLease,environment_factory=lambda:(self.profile,model),
                                   observer=observe,audit_factory=factory)
        self.assertEqual(report['result'],'passed');self.assertEqual(public['model_calls'],1)
        self.assertTrue(report['audit']['verified']);self.assertTrue(report['cleanup_verified'])
        self.assertEqual(len(report['audit']['records']),3)


if __name__=='__main__':unittest.main()
