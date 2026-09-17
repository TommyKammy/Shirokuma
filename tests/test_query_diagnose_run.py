"""Source integrity, one model invocation, public completion and interruption."""
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from tools.local_experiment.query_diagnose import run as R, contract as D
from test_query_diagnose_contract import source, projections

TOKEN = 'b' * 32


def receipt():
    s = source(); request = s['request']; selected = s['selection']
    sql = [R.C.discovery_sql(request), R.C.schema_sql(request),
           R.C.select_sql(request, s['inventory'], s['identity']['columns'], s['identity']), R.C.schema_sql(request)]
    workers = []
    for i, (label, statement) in enumerate(zip(('inventory', 'schema_before', 'select', 'schema_after'), sql)):
        workers.append({'label': label, 'query_id': s['query_id'] if i == 2 else f'20260918_000000_{i+10:05d}_abcde',
            'operation_id': str(uuid.uuid4()), 'result': 'finished', 'response_complete': True, 'post_attempted': True,
            'error_code': None, 'http_status': 200, 'pages': 1, 'response_bytes': 1000, 'server_max_run_seconds': 30,
            'sql_sha256': hashlib.sha256(statement.encode()).hexdigest(),
            'row_count': selected['fetched_row_count'] if i == 2 else 1})
    control = deepcopy(workers[0]); control.update(label='drain', query_id='20260918_000000_00020_abcde',
        operation_id=str(uuid.uuid4()), sql_sha256=hashlib.sha256(R.Q.inspection_sql(s['read_token']).encode()).hexdigest())
    catalog = {'authenticated': True, 'authentication_attempted': True, 'reads': [
        {'result': 'verified', 'metadata_sha256': 'a'*64, 'request_sha256': R.C.digest(request)}]*2}
    queries = {'worker': workers, 'control': [control]}
    runtime = {'state': 'verified', 'cleanup': {'verified': True}, 'catalog': catalog}
    for role, flag in (('worker', False), ('control', True)):
        user, src = R.Q.identity(s['read_token'], flag)
        runtime[role] = {'user': user, 'source': src, 'records': queries[role], 'cancellations': []}
    for component, name in (('trino', 'trino-local'), ('polaris', 'polaris')):
        runtime[component] = {'state': 'verified', 'service': {'name': name, 'namespace': 'shirokuma-dev',
            'uid': str(uuid.uuid4()), 'spec_sha256': 'e'*64}}
    return {'schema_version': 1, 'evidence_token': s['read_token'], 'result': 'passed', 'invocations': 1,
        'errors': [], 'error_code': None, 'started_at': '2026-09-18T00:00:00+00:00', 'finished_at': '2026-09-18T00:01:00+00:00',
        'request': request, 'request_sha256': R.C.digest(request), 'inventory': s['inventory'],
        'identity_before': s['identity'], 'identity_after': s['identity'], 'identity_unchanged': True,
        'cleanup': {'verified': True}, 'source_hashes': {p: 'a'*64 for p in R.R.sources()}, 'queries': queries,
        'runtime': runtime, 'catalog': catalog, 'observation': selected, 'limits': ['LOCAL_EXPERIMENT_ONLY'],
        'query_drain': {'verified': True, 'user': s['user'], 'source': s['source'], 'active': [], 'cancelled': [],
            'cancel_errors': [], 'errors': [], 'observed_query_ids': sorted(row['query_id'] for row in workers)}}


class SourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve(); self.base.chmod(0o700)
        self.value = receipt(); self.token = source()['read_token']
        self.folder = self.base / self.token; self.folder.mkdir(mode=0o700)
        self.path = self.folder / 'summary.json'
        (self.folder / 'run.lock').touch(mode=0o600)
        self.write()
        patcher = patch.object(R, 'READ_STATE', self.base); patcher.start(); self.addCleanup(patcher.stop)

    def write(self):
        self.path.write_bytes(R.C.canonical(self.value)); self.path.chmod(0o600)

    def test_successful_historical_source_digests_are_not_current_code_gate(self):
        original = self.path.read_bytes(); before = R.fingerprint(self.path.stat())
        with R.SourceLease(self.token) as lease:
            self.assertEqual(lease.source['query_id'], source()['query_id'])
            self.assertEqual(lease.source['summary_sha256'], hashlib.sha256(original).hexdigest())
            lease.unchanged()
        self.assertEqual(R.fingerprint(self.path.stat()), before)
        self.assertEqual(self.path.read_bytes(), original)

    def test_busy_source_is_rejected_without_file_changes(self):
        with open(self.folder/'run.lock', 'rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(R.DiagnoseError, '^SOURCE_BUSY$'):
                with R.SourceLease(self.token): pass

    def test_source_identity_terminal_query_and_hash_mismatch_rejected(self):
        original = deepcopy(self.value)
        changes = [lambda v:v.update(result='failed'), lambda v:v.update(invocations=True),
            lambda v:v.update(error_code='PRIVATE-TEXT'), lambda v:v['cleanup'].update(verified=False),
            lambda v:v['runtime']['trino'].update(state='failed'),
            lambda v:v['queries']['worker'][2].update(sql_sha256='0'*64),
            lambda v:v['queries']['worker'][2].update(response_complete=False),
            lambda v:v['queries']['worker'][2].update(query_id=v['queries']['worker'][0]['query_id']),
            lambda v:v['query_drain'].update(source='foreign'),
            lambda v:v['catalog']['reads'][0].update(request_sha256='0'*64),
            lambda v:v['catalog']['reads'][0].update(metadata_sha256='not-a-hash'),
            lambda v:v['identity_after'].update(current_snapshot_id='1'),
            lambda v:v['observation'].update(rows=[['private']]),
            lambda v:v['runtime']['worker'].update(user='foreign')]
        for change in changes:
            with self.subTest(change=change):
                self.value = deepcopy(original); change(self.value); self.write()
                with self.assertRaises(R.DiagnoseError):
                    with R.SourceLease(self.token): pass

    def test_duplicate_json_and_nonprivate_source_rejected(self):
        self.path.write_bytes(b'{"result":"passed","result":"failed"}')
        with self.assertRaises(R.DiagnoseError):
            with R.SourceLease(self.token): pass
        self.write(); self.path.chmod(0o644)
        with self.assertRaises(R.DiagnoseError):
            with R.SourceLease(self.token): pass

    def test_source_change_after_acquiring_lease_detected(self):
        with R.SourceLease(self.token) as lease:
            self.value['finished_at'] = '2026-09-18T00:02:00+00:00'; self.write()
            with self.assertRaisesRegex(R.DiagnoseError, '^SOURCE_CHANGED$'): lease.unchanged()


class FakeLease:
    def __init__(self, token): self.source = source(); self.closed = False
    def __enter__(self): return self
    def unchanged(self): pass
    def __exit__(self, *_): self.closed = True


class FakeAudit:
    def __init__(self, token, output, profile, progress):
        self.progress, self.actions, self.finished = progress, [], False
    def start(self): self.progress({'verified': False})
    def phase(self, action, call, *, inference=False, validate=None):
        self.actions.append(action); value = call(); return validate(value) if validate else value
    def finish(self): self.finished = True
    def close(self, original=None): self.progress({'verified': original is None and self.finished})


def observation():
    return {'state': 'verified', 'cleanup_verified': True, **projections(),
            'identity_before': source()['identity'], 'identity_after': source()['identity']}


class OwnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.output = Path(tmp.name).resolve(); self.output.chmod(0o700)
        self.lease = FakeLease(source()['read_token'])
        self.model = Mock()
        self.model.complete.side_effect = lambda phase, evidence: {'content': D.expected_diagnosis(evidence)}
        self.observe = Mock(side_effect=self.observed)
        self.env = Mock(return_value=({'provider':'local','model':'qwen3.8-flash-next'}, self.model))
        self.owner = R.Owner(TOKEN, source()['read_token'], self.output)

    def observed(self, src, token, progress):
        self.assertEqual(self.model.complete.call_count, 0)
        value = observation(); progress(value); return value

    def execute(self, **kwargs):
        return self.owner.execute(lease_factory=lambda _:self.lease, environment_factory=self.env,
            observer=self.observe, audit_factory=kwargs.pop('audit_factory', FakeAudit), **kwargs)

    def test_public_only_after_observation_model_audit_and_cleanup(self):
        report, public = self.execute()
        self.assertEqual(report['result'], 'passed'); self.assertTrue(report['audit']['verified'])
        self.assertTrue(report['cleanup_verified']); self.assertEqual(self.model.complete.call_count, 1)
        self.assertFalse(public['original_select_reexecuted']); self.assertTrue(self.lease.closed)
        self.assertEqual(public, D.public_summary(source(), report['model_evidence'], report['diagnosis']))
        saved = (self.output/'summary.json').read_text()
        self.assertNotIn('SELECT ', saved); self.assertNotIn('private-row', saved)
        self.assertEqual((self.output/'summary.json').stat().st_mode&0o777, 0o600)
        with self.assertRaises(R.DiagnoseError): self.execute()
        self.assertEqual(self.observe.call_count, 1)

    def test_source_failure_is_before_environment_runtime_model_or_audit(self):
        with patch.object(self.lease, '__enter__', side_effect=R.DiagnoseError('SOURCE_BUSY')):
            report, public = self.execute()
        self.assertIsNone(public); self.assertEqual(report['error_code'], 'SOURCE_BUSY')
        self.env.assert_not_called(); self.observe.assert_not_called(); self.model.complete.assert_not_called()

    def test_observation_failure_stops_model(self):
        self.observe.side_effect = ValueError('PRIVATE BODY')
        report, public = self.execute()
        self.assertIsNone(public); self.assertEqual(report['error_code'], 'OBSERVATION_FAILED')
        self.model.complete.assert_not_called(); self.assertNotIn('PRIVATE BODY', (self.output/'summary.json').read_text())

    def test_model_plan_rejected_without_retries(self):
        self.model.complete.side_effect = None; self.model.complete.return_value = {'content': {'sql':'PRIVATE SQL'}}
        report, public = self.execute()
        self.assertIsNone(public); self.assertEqual(report['error_code'], 'MODEL_REJECTED')
        self.assertEqual(self.model.complete.call_count, 1); self.assertTrue(report['cleanup_verified'])
        self.assertNotIn('PRIVATE SQL', (self.output/'summary.json').read_text())

    def test_unknown_model_failure_is_not_replayed_or_exposed(self):
        self.model.complete.side_effect = OSError('PRIVATE MODEL RESPONSE')
        report, public = self.execute()
        self.assertIsNone(public); self.assertEqual(report['error_code'], 'MODEL_FAILED')
        self.assertEqual(self.model.complete.call_count, 1)
        self.assertNotIn('PRIVATE MODEL RESPONSE', (self.output/'summary.json').read_text())

    def test_first_cancellation_survives_audit_close_failure(self):
        signal = KeyboardInterrupt(); self.model.complete.side_effect = signal
        class BadClose(FakeAudit):
            def close(self, original=None): raise OSError('secondary')
        with self.assertRaises(KeyboardInterrupt) as raised: self.execute(audit_factory=BadClose)
        self.assertIs(raised.exception, signal)
        self.assertEqual(self.owner.report['result'], 'interrupted'); self.assertTrue(self.lease.closed)

    def test_audit_close_failure_withholds_success(self):
        class BadClose(FakeAudit):
            def close(self, original=None): raise OSError('secondary')
        report, public = self.execute(audit_factory=BadClose)
        self.assertIsNone(public); self.assertEqual(report['error_code'], 'AUDIT_FAILED')

    def test_source_changes_after_observation_stop_model(self):
        self.lease.unchanged = Mock(side_effect=R.DiagnoseError('SOURCE_CHANGED'))
        report, public = self.execute()
        self.assertIsNone(public); self.assertEqual(report['error_code'], 'SOURCE_CHANGED')
        self.model.complete.assert_not_called()

    def test_final_persistence_failure_withholds_success(self):
        original = self.owner.save
        def fail():
            if self.owner.report['result'] == 'passed': raise OSError('disk')
            original()
        with patch.object(self.owner, 'save', side_effect=fail): report, public = self.execute()
        self.assertIsNone(public); self.assertEqual(report['result'], 'failed')


class MainTests(unittest.TestCase):
    def invoke(self, request=None, invoker=None, output=None):
        output = output or io.StringIO()
        code = R.main(['diagnose','--evidence-token',TOKEN,'--request-stdin'], stdout=output,
            stdin=io.BytesIO(json.dumps(request or {'read_token':source()['read_token']}).encode()), invoker=invoker)
        return code, [json.loads(s) for s in output.getvalue().splitlines()]

    def test_actual_main_invoke_owner_with_fake_external_boundaries(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve(); base.chmod(0o700)
            model = Mock(); model.complete.side_effect = lambda phase,evidence:{'content':D.expected_diagnosis(evidence)}
            def observe(src, token, progress):
                value = observation(); progress(value); return value
            def invoke(token, read_token, emit):
                return R.invoke(token, read_token, emit, lease_factory=FakeLease,
                    environment_factory=lambda:({'provider':'local','model':'fake'}, model),
                    observer=observe, audit_factory=FakeAudit)
            with patch.object(R, 'STATE', base):
                code, events = self.invoke(invoker=invoke)
                self.assertEqual(code, 0); self.assertEqual([v['event'] for v in events], ['allocated','result'])
                self.assertEqual(events[1]['result'], 'passed')
                before = (base/TOKEN/'summary.json').read_bytes()
                code, events = self.invoke(invoker=invoke)
                self.assertEqual(code, 1); self.assertEqual(events[0]['error_code'], 'EVIDENCE_ALREADY_EXISTS')
                self.assertEqual((base/TOKEN/'summary.json').read_bytes(), before)
                self.assertEqual(model.complete.call_count, 1)

    def test_invalid_request_before_invocation(self):
        run = Mock()
        for value in ({'read_token':TOKEN}, {'read_token':'../file'}, {'read_token':'a'*32,'sql':'SELECT'}):
            code, events = self.invoke(value, run)
            self.assertEqual(code, 1); self.assertEqual(events[0]['error_code'], 'REQUEST_INVALID')
        run.assert_not_called()

    def test_workflow_systemexit_zero_is_not_success(self):
        code, events = self.invoke(invoker=Mock(side_effect=SystemExit(0)))
        self.assertEqual(code, 130); self.assertEqual(events[0]['result'], 'interrupted')

    def test_untyped_exception_text_is_not_public(self):
        code, events = self.invoke(invoker=Mock(side_effect=RuntimeError('PRIVATE RESPONSE')))
        self.assertEqual(code, 1); self.assertEqual(events[0]['error_code'], 'DIAGNOSE_FAILED')
        self.assertNotIn('PRIVATE RESPONSE', json.dumps(events))


if __name__ == '__main__': unittest.main()
