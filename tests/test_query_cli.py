"""Query bridge: synthetic saved evidence and fake workflows, no live services."""
import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
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
from unittest.mock import patch, Mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment/bears'))
import query_cli as CLI

TOKEN, INNER, TRACE = '1' * 32, '2' * 32, '3' * 32
PRIVATE = 'PRIVATE_SQL_TOKEN_RESPONSE_PATH_SENTINEL'


def fixture_documents():
    C = CLI.C
    stamp = '2026-09-17T06:00:00.000000Z'
    cases, entries, requests = [], [], []
    for i, (name, count) in enumerate(zip(C.CASES, (2, 1))):
        ids = {kind: f'q_{i}_{kind}' for kind in ('select', 'explain')}
        nodes = [dict(id=f'p{n:02}', kind=kind, columns=[{'name': 'id', 'type': 'bigint'}],
            filter_present=kind == 'ScanFilter', estimates=[dict.fromkeys(C.ESTIMATE_FIELDS, None)])
            for n, kind in enumerate(('Output', 'TableScan' if i == 0 else 'ScanFilter'), 1)]
        cases.append(dict(id=name, select=dict(query_id=ids['select'], row_count=count, matched=True),
            explain=dict(query_id=ids['explain'], plan=dict(format='trino-483-distributed-json',
                fragment_count=1, nodes=nodes, estimates_complete=False))))
        for kind, qid in ids.items():
            entries.append(dict(query_id=qid, case_id=name, kind=kind, state='FINISHED', created_at=stamp,
                started_at=stamp, ended_at=stamp, error_type=None, error_code=None, **dict.fromkeys(C.TIMING_FIELDS, 0)))
            requests.append(dict(case_id=name, kind=kind, query_id=qid, state='FINISHED', response_complete=True))
    observation = dict(schema_version=1, run_token=INNER, observed_at=stamp, cases=cases, performance_gain='unmeasured',
        history=dict(query_id='q_history', observed_at=stamp, completeness='complete', missing_query_ids=[],
                     entries=entries, durations_complete=True))
    stats = dict(query_id='q_stats', observed_at=stamp, table='fixture_v1', row_count_estimate=2,
        columns=[dict(name=name, data_size=None, distinct_values_count=None, nulls_fraction=None) for name in ('id', 'name')],
        estimates_complete=False)
    requests.extend([dict(case_id=None, kind=kind, query_id=qid, state='FINISHED', response_complete=True)
                     for kind, qid in [('own_query_history', 'q_history'), ('fixture_stats', 'q_stats')]])
    evidence = C.project_observation(observation)
    plan = C.expected_plan(evidence)
    records, spans = [], []
    actions = ['bear.query.observe', 'llm.query_plan', 'bear.query.fixture_stats', 'bear.query.assess']
    metrics = dict(prompt_tokens=10, completion_tokens=20, duration_ms=3, inference_cost_estimate=None)
    for i, action in enumerate(actions, 1):
        sid = f'{i:016x}'
        records.append(dict(event_id=str(uuid.UUID(int=i)), task_id='workflow-' + INNER, trace_id=TRACE,
            action_type=action, status='success', model_provider='local' if action == 'llm.query_plan' else 'none',
            model_name='fixed' if action == 'llm.query_plan' else '', span_id=sid, audit_delivery='delivered',
            **(metrics if action == 'llm.query_plan' else dict(prompt_tokens=None, completion_tokens=None,
                inference_cost_estimate=None, duration_ms=1))))
        spans.append(dict(name=action, trace_id=TRACE, span_id=sid, start_time_unix_nano=1 if i == 4 else i+1,
            end_time_unix_nano=100 if i == 4 else i+2, status='OK',
            attributes={'task_id': 'workflow-' + INNER} if i == 4 else {}))
    result = dict(result='passed', scope='fixed-two-row-fixture-only', task_id='workflow-' + INNER,
        query_run_token=INNER, trace_id=TRACE, provider='local', model='fixed', audit_records=records,
        retries=0, cloud_calls=0, sql_rewrites=0, root_cause='undetermined', performance_gain='unmeasured',
        cleanup_verified=True, plan=plan, inference=metrics, assessment=C.assess_followups(observation, plan, {'fixture_stats': stats}),
        checkpoint='completed-in-memory', correlated_events=4, saved_otel_spans_verified=4)
    receipt = dict(schema_version=1, run_token=INNER, outcome='completed', server_max_run_seconds=30,
        cleanup_verified=True, target_history_verified=True, requests=requests, uncaptured_requests=0)
    return {'result.json': result, 'spans.jsonl': spans, 'observer-receipt.json': receipt,
        'observation.json': observation, 'model-evidence.json': evidence, 'plan.json': plan, 'fixture_stats.json': stats}


def encode(documents):
    return {name: ('\n'.join(CLI.canonical(v) for v in value) if name.endswith('jsonl') else CLI.canonical(value)).encode()
            for name, value in documents.items()}


def save(path, value):
    path.write_bytes(encode({path.name: value})[path.name] + b'\n')
    path.chmod(0o600)


def write_fixture(output, documents=None):
    output.mkdir(mode=0o700)
    documents = fixture_documents() if documents is None else documents
    for name, value in documents.items():
        save(output / name, value)
    return documents['result.json']


class QueryCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state, self.evidence = self.root / 'query-cli', self.root / 'query-bear'
        for key, value in [('STATE', self.state), ('EVIDENCE', self.evidence)]:
            p = patch.object(CLI, key, value); p.start(); self.addCleanup(p.stop)
        async def run(config, output):
            return write_fixture(output)
        self.fake = SimpleNamespace(load_config=Mock(return_value={'active_provider': 'local'}),
            validate_config=Mock(), SESSION=SimpleNamespace(interruption_handlers=nullcontext), run=Mock(side_effect=run))
        p = patch.object(CLI, 'runner', return_value=self.fake); self.module = p.start(); self.addCleanup(p.stop)

    def main(self, action='run', token=TOKEN, args=None):
        out = io.StringIO()
        code = CLI.main(args if args is not None else [action, '--evidence-token', token] +
            (['--case', 'retained-fixture'] if action == 'run' else []), stdout=out)
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertIn(len(rows), (1, 2)); self.assertEqual(set(rows[-1]), CLI.FINAL_KEYS)
        self.assertNotIn(PRIVATE, out.getvalue()); self.assertNotIn(str(self.root), out.getvalue())
        return code, rows

    def files(self):
        return {str(p.relative_to(self.root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode, p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_run_single_invocation_and_reuse_refusal(self):
        code, rows = self.main()
        self.assertEqual(code, 0); self.assertEqual(rows[-1]['query_run_token'], INNER)
        self.assertNotEqual(rows[-1]['evidence_token'], rows[-1]['query_run_token'])
        self.assertEqual(rows[0], dict(schema_version=1, event='allocated', command='run', evidence_token=TOKEN))
        self.assertEqual(rows[-1]['verification'], 'saved-evidence')
        before = self.files()
        code, rows = self.main()
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['error_code'], 'QUERY_ALREADY_EXISTS')
        self.assertEqual(rows[-1]['execution'], 'not-started')
        self.assertEqual(self.fake.run.call_count, 1); self.assertEqual(self.files(), before)

    def test_invalid_arguments_do_not_import_or_call_workflow(self):
        for args in ([], ['run'], ['run', '--evidence-token', TOKEN],
            ['run', '--evidence-token', TOKEN, '--case', PRIVATE], ['status', '--evidence-token', '../' + PRIVATE],
            ['resume', '--evidence-token', TOKEN], ['run', '--evidence-token', TOKEN, '--case', 'retained-fixture', '--sql', PRIVATE]):
            code, rows = self.main(args=args)
            self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'not_started')
        self.module.assert_not_called(); self.fake.run.assert_not_called()

    def test_environment_failure_precedes_intent_and_does_not_install(self):
        self.module.side_effect = ImportError(PRIVATE)
        code, rows = self.main()
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['error_code'], 'ENVIRONMENT_NOT_READY')
        self.assertEqual(rows[-1]['execution'], 'not-started'); self.assertFalse((self.state / TOKEN).exists())

    def test_durable_intent_failure_prevents_workflow(self):
        original = CLI.persist
        def fail_after_write(*args):
            original(*args)
            raise OSError(PRIVATE)
        with patch.object(CLI, 'persist', side_effect=fail_after_write):
            code, rows = self.main()
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['execution'], 'not-started')
        self.fake.run.assert_not_called()
        code, rows = self.main('status')
        self.assertEqual(code, 0); self.assertEqual(rows[-1]['result'], 'unknown')
        self.assertEqual(rows[-1]['verification'], 'journal-only')

    def test_parent_fsync_failure_prevents_invocation(self):
        with patch.object(CLI, 'sync', side_effect=OSError(PRIVATE)):
            code, rows = self.main()
        self.assertEqual(code, 1); self.fake.run.assert_not_called()

    def test_invocation_begins_only_after_durable_intent(self):
        async def invoke(config, output):
            journal = CLI.loads(CLI.read(self.state / TOKEN / 'intent.json'))
            self.assertEqual(journal['invocations'], 1); self.assertEqual(journal['state'], 'invoked')
            with CLI.lock(self.state / TOKEN / 'run.lock') as busy:
                self.assertTrue(busy)
            return write_fixture(output)
        self.fake.run.side_effect = invoke
        self.assertEqual(self.main()[0], 0)

    def test_unknown_exception_has_safe_output_and_never_retries(self):
        async def failure(*args):
            print(PRIVATE); print(PRIVATE, file=sys.stderr); raise RuntimeError(PRIVATE)
        self.fake.run.side_effect = failure
        code, rows = self.main()
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'unknown')
        self.assertEqual(rows[-1]['execution'], 'invoked')
        self.main(); self.assertEqual(self.fake.run.call_count, 1)

    def test_cancellation_kept_even_if_second_journal_write_fails(self):
        async def cancelled(*args): raise asyncio.CancelledError()
        self.fake.run.side_effect = cancelled
        original = CLI.persist
        def fail_final(directory, journal):
            if journal['state'] == 'interrupted': raise OSError(PRIVATE)
            return original(directory, journal)
        with patch.object(CLI, 'persist', side_effect=fail_final):
            code, rows = self.main()
        self.assertEqual(code, 130); self.assertEqual(rows[-1]['error_code'], 'INTERRUPTED')
        self.assertEqual(rows[-1]['result'], 'unknown')

    def test_returned_value_is_bound_to_saved_result(self):
        async def mismatch(config, output):
            value = write_fixture(output); return dict(value, query_run_token='4'*32)
        self.fake.run.side_effect = mismatch
        code, rows = self.main()
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['error_code'], 'QUERY_EVIDENCE_INVALID')

    def test_status_completed_is_readonly_and_model_config_free(self):
        self.assertEqual(self.main()[0], 0)
        before = self.files()
        self.module.side_effect = AssertionError('status imported live runner')
        for _ in range(2):
            code, rows = self.main('status')
            self.assertEqual(code, 0); self.assertEqual(rows[-1]['result'], 'passed')
        self.assertEqual(self.files(), before)
        self.assertEqual(self.fake.load_config.call_count, 1); self.assertEqual(self.fake.run.call_count, 1)

    def test_legacy_success_without_cli_lock_accepts_separately_and_no_write(self):
        self.evidence.mkdir(mode=0o755)
        write_fixture(self.evidence / TOKEN)
        before = self.files()
        code, rows = self.main('status')
        self.assertEqual(code, 0); self.assertEqual(rows[-1]['result'], 'passed')
        self.assertEqual(self.files(), before); self.assertFalse(self.state.exists()); self.module.assert_not_called()

    def test_busy_does_not_read_unstable_workflow_files(self):
        self.main()
        before = self.files()
        with (self.state / TOKEN / 'run.lock').open('rb') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(CLI, 'inspect_saved', side_effect=AssertionError('deep read while active')):
                code, rows = self.main('status')
        self.assertEqual(code, 0); self.assertEqual(rows[-1]['result'], 'busy')
        self.assertEqual(self.files(), before)

    def test_initial_failed_receipt_without_closed_root_remains_unknown(self):
        self.evidence.mkdir(mode=0o700)
        docs = fixture_documents()
        docs['result.json'].update(result='failed')
        for key in ('checkpoint', 'assessment', 'plan', 'inference', 'saved_otel_spans_verified', 'correlated_events'):
            docs['result.json'].pop(key, None)
        docs['result.json']['audit_records'] = docs['result.json']['audit_records'][:1]
        docs['spans.jsonl'] = docs['spans.jsonl'][:1]
        for name in ('plan.json', 'fixture_stats.json'):
            docs.pop(name)
        docs['observer-receipt.json']['requests'] = docs['observer-receipt.json']['requests'][:5]
        write_fixture(self.evidence / TOKEN, docs)
        code, rows = self.main('status')
        self.assertEqual(code, 0); self.assertEqual(rows[-1]['result'], 'unknown')
        self.assertEqual(rows[-1]['verification'], 'journal-only')

    def test_closed_failed_root_is_terminal_failure_not_success(self):
        self.evidence.mkdir(mode=0o700)
        docs = fixture_documents(); result = docs['result.json']
        result.update(result='failed', error_code='QUERY_OBSERVATION_PLAN_FOLLOWUP_OR_AUDIT_FAILED')
        result.pop('checkpoint'); result['audit_records'][-1]['status'] = 'failed'
        docs['spans.jsonl'][-1]['status'] = 'ERROR'
        write_fixture(self.evidence / TOKEN, docs)
        code, rows = self.main('status')
        self.assertEqual(code, 0); self.assertEqual(rows[-1]['result'], 'failed')
        self.assertEqual(rows[-1]['verification'], 'saved-evidence')

    def test_observer_identity_and_missing_request_tampering_rejected(self):
        for edit in (lambda d: d['observer-receipt.json'].update(run_token=TOKEN),
                     lambda d: d['observer-receipt.json']['requests'].pop(),
                     lambda d: d['observer-receipt.json']['requests'][0].update(query_id='foreign'),
                     lambda d: d['observer-receipt.json']['requests'][0].update(response_complete=False),
                     lambda d: d['observation.json']['history']['entries'][0].update(query_id='foreign')):
            docs = fixture_documents(); edit(docs)
            with self.assertRaises((ValueError, KeyError)): CLI.verify_documents(encode(docs))

    def test_span_audit_and_assessment_tampering_rejected(self):
        for edit in (lambda d: d['spans.jsonl'][-1].update(trace_id=TOKEN),
                     lambda d: d['spans.jsonl'][-1].update(end_time_unix_nano=None),
                     lambda d: d['spans.jsonl'][-1]['attributes'].update(task_id='workflow-' + TOKEN),
                     lambda d: d['result.json']['audit_records'][1].update(status='blocked'),
                     lambda d: d['result.json']['assessment'].update(root_cause=PRIVATE),
                     lambda d: d['result.json']['inference'].update(prompt_tokens=11)):
            docs = fixture_documents(); edit(docs)
            with self.assertRaises((ValueError, KeyError)): CLI.verify_documents(encode(docs))

    def test_completed_hash_seal_rejects_even_semantically_valid_change(self):
        self.main()
        path = self.evidence / TOKEN / 'result.json'
        value = json.loads(path.read_text()); value['model'] = 'other'; save(path, value)
        before = self.files()
        code, rows = self.main('status')
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'unknown')
        self.assertEqual(self.files(), before)

    def test_status_missing_symlink_hardlink_and_public_files_fail_without_write(self):
        code, rows = self.main('status')
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'not_found'); self.assertFalse(self.state.exists())
        self.evidence.mkdir(mode=0o700)
        (self.evidence / TOKEN).symlink_to(self.root / 'absent')
        code, rows = self.main('status'); self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'unknown')
        (self.evidence / TOKEN).unlink(); write_fixture(self.evidence / TOKEN)
        f = self.evidence / TOKEN / 'result.json'; f.chmod(0o644)
        self.assertEqual(self.main('status')[0], 1)
        f.chmod(0o600); os.link(f, self.root / 'copy')
        self.assertEqual(self.main('status')[0], 1)

    def test_partial_journal_or_missing_lock_does_not_trigger_run(self):
        self.main()
        (self.state / TOKEN / 'run.lock').unlink()
        before = self.files()
        self.assertEqual(self.main('status')[0], 1); self.assertEqual(self.files(), before)
        self.assertEqual(self.fake.run.call_count, 1)

    def test_source_changed_between_reads_fails_closed(self):
        self.main()
        original = CLI.snapshot
        calls = []
        def drifting(path):
            value = original(path); calls.append(path)
            if len(calls) == 1:
                f = path / 'result.json'; v = json.loads(f.read_text()); v['model'] = 'changed'; save(f, v)
            return value
        with patch.object(CLI, 'snapshot', side_effect=drifting):
            code, rows = self.main('status')
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'unknown')

    def test_history_terminal_claim_must_agree_with_latest_observation(self):
        docs = fixture_documents()
        docs['observer-receipt.json']['requests'][0]['state'] = 'FAILED'
        with self.assertRaises(ValueError): CLI.verify_documents(encode(docs))

    def test_integer_millisecond_history_matches_observer_without_float_rounding(self):
        from query_doctor.observe import project_history, HISTORY_COLUMNS
        C = CLI.C
        captured = {f'q_{i}_{kind}': {'case_id': name, 'kind': kind}
                    for i, name in enumerate(C.CASES) for kind in ('select', 'explain')}
        begin = datetime(2026, 9, 17, 14, 14, 18, tzinfo=timezone.utc)
        stamp = lambda value: value.isoformat(timespec='microseconds').replace('+00:00', 'Z')
        for milliseconds, micros in [(ms, us) for ms in (1001, 1003, 1011, 0, 86399999, 86401011) for us in (0, 999)]:
            with self.subTest(milliseconds=milliseconds, micros=micros):
                end = begin + timedelta(milliseconds=milliseconds, microseconds=micros)
                raw = {'query_id': 'own_history', 'columns': HISTORY_COLUMNS, 'rows': [
                    [qid, 'FINISHED', 'shirokuma-query-doctor-' + INNER, 0, 0, 0,
                     stamp(begin), stamp(begin), stamp(end), None, None] for qid in captured]}
                history = project_history(raw, captured, 'shirokuma-query-doctor-' + INNER)
                self.assertEqual(history['entries'][0]['started_to_end_ms'], milliseconds)
                self.assertTrue(C.history_summary(history, captured)['durations_complete'])
                history['entries'][0]['started_to_end_ms'] -= 1
                with self.assertRaises(C.ContractError): C.history_summary(history, captured)
        # The first live CLI failure was exactly 1011 ms, not a noisy sub-ms value.
        raw['rows'] = [[qid, 'FINISHED', 'shirokuma-query-doctor-' + INNER, 0, 0, 0,
                       stamp(begin), stamp(begin), stamp(begin + timedelta(milliseconds=1011)), None, None]
                      for qid in captured]
        history = project_history(raw, captured, 'shirokuma-query-doctor-' + INNER)
        self.assertEqual(history['entries'][0]['started_to_end_ms'], 1011)
        self.assertTrue(C.history_summary(history, captured)['durations_complete'])

    def test_broken_parent_symlink_is_invalid_not_missing(self):
        self.evidence.symlink_to(self.root / 'missing-parent', target_is_directory=True)
        code, rows = self.main('status')
        self.assertEqual(code, 1); self.assertEqual(rows[-1]['result'], 'unknown')
        self.assertFalse(self.state.exists()); self.module.assert_not_called()

    def test_exact_stdout_shape_and_success_requirements(self):
        _, lines = self.main(); valid = lines[-1]
        for changes in ({'cleanup_verified': False}, {'query_run_token': None}, {'verification': 'journal-only'},
                        {'evidence': '/private/other'}, {'error_code': 'QUERY_FAILED'}, {'sql': PRIVATE}):
            with self.assertRaises(ValueError): CLI.validate_projection(dict(valid, **changes))

    def test_isolated_status_subprocess_requires_no_optional_dependencies(self):
        process = subprocess.run([sys.executable, '-I', '-B', str(Path(CLI.__file__)), 'status', '--evidence-token', '0'*32],
            capture_output=True, text=True, timeout=10, cwd=self.root,
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','USER','LANG')})
        self.assertEqual(process.returncode, 1); self.assertEqual(process.stderr, '')
        values = [json.loads(x) for x in process.stdout.splitlines()]
        self.assertEqual(values[-1]['result'], 'not_found'); self.assertEqual(set(values[-1]), CLI.FINAL_KEYS)


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires pinned LLM environment')
class CancellationTests(unittest.TestCase):
    def test_existing_runner_with_fake_services_passes_bridge_saved_validator(self):
        import run_query as R
        from test_local_query_bear import config, AuditClient, EVALUATE
        agenthouse, client, logs = EVALUATE.agenthouse_module(), AuditClient(), []
        loki = Mock()
        loki.push_record.side_effect = lambda value: logs.append(value)
        loki.query_task.side_effect = lambda task: [r for r in logs if r['task_id'] == task]
        class Observer:
            def __init__(self, url, run_token):
                self.docs = fixture_documents()
                self.docs['observation.json']['run_token'] = run_token
                self.docs['observer-receipt.json']['run_token'] = run_token
                self.followed = False
            def collect(self): return self.docs['observation.json']
            def followup(self, tool):
                self.followed = True
                return self.docs[tool + '.json']
            def receipt(self):
                value = dict(self.docs['observer-receipt.json'])
                if not self.followed: value['requests'] = value['requests'][:5]
                return value
        def complete(phase, evidence):
            return dict(content=CLI.C.expected_plan(evidence), prompt_tokens=10, completion_tokens=20,
                        inference_cost_estimate=None, duration_ms=3)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp).resolve() / 'output'
            with patch.object(R, 'QueryProvider', return_value=SimpleNamespace(complete=complete)), \
                 patch.object(R, 'QueryObserver', Observer), patch.object(R.SESSION, 'check_context'), \
                 patch.object(R.SESSION, 'trino_tunnel', return_value=nullcontext('http://127.0.0.1:9999')), \
                 patch.object(R, 'agenthouse_module', return_value=agenthouse), patch.object(agenthouse, 'Client', return_value=client), \
                 patch.object(R, 'module', return_value=loki):
                result = asyncio.run(R.run(config(), output))
            self.assertEqual(result['result'], 'passed')
            view = CLI.verify_documents(CLI.snapshot(output)[0])
            self.assertEqual(view['result'], 'passed')
            self.assertEqual(view['query_run_token'], result['query_run_token'])

    def cancel_run(self, fail_file):
        import run_query as R
        from test_local_query_bear import config, AuditClient, EVALUATE
        error = asyncio.CancelledError()
        agenthouse = EVALUATE.agenthouse_module()
        client = AuditClient()
        class Observer:
            def __init__(self, url, run_token): self.token = run_token
            def receipt(self):
                return dict(schema_version=1, run_token=self.token, outcome='incomplete', server_max_run_seconds=30,
                    cleanup_verified=False, target_history_verified=False, requests=[], uncaptured_requests=0)
        class Graph:
            async def ainvoke(self, *args): raise error
        original = R.private_write
        def write(path, value):
            if path.name == fail_file and (fail_file == 'observer-receipt.json' or value.get('result') == 'interrupted'):
                raise OSError(PRIVATE)
            return original(path, value)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            with patch.object(R, 'QueryProvider'), patch.object(R, 'QueryObserver', Observer), \
                 patch.object(R.SESSION, 'check_context'), patch.object(R.SESSION, 'trino_tunnel', return_value=nullcontext('http://127.0.0.1:9999')), \
                 patch.object(R, 'agenthouse_module', return_value=agenthouse), patch.object(agenthouse, 'Client', return_value=client), \
                 patch.object(R, 'module', return_value=Mock()), patch.object(R, 'build_graph', return_value=Graph()), \
                 patch.object(R, 'private_write', side_effect=write):
                with self.assertRaises(asyncio.CancelledError) as caught:
                    asyncio.run(R.run(config(), output))
            self.assertIs(caught.exception, error)
            spans = [json.loads(x) for x in (output / 'spans.jsonl').read_text().splitlines()]
            self.assertEqual(spans[-1]['name'], 'bear.query.assess'); self.assertEqual(spans[-1]['status'], 'ERROR')
            self.assertEqual(client.events[-1]['status'], 'failed')

    def test_original_run_cancellation_survives_final_receipt_write_failure(self):
        self.cancel_run('result.json')

    def test_graph_cancellation_survives_observer_finally_write_failure(self):
        self.cancel_run('observer-receipt.json')

    def test_model_cancellation_survives_failed_audit_and_never_follows_up(self):
        import run_query as R
        from test_local_query_bear import observation, FakeTracer
        from unittest.mock import AsyncMock
        import threading
        async def cancel():
            started, release = threading.Event(), threading.Event()
            def complete(*args):
                started.set(); release.wait(10)
                return {}
            def audit(action, status, *args, **kwargs):
                if action == 'llm.query_plan': raise OSError(PRIVATE)
            followup = AsyncMock()
            graph = R.build_graph(SimpleNamespace(complete=complete), AsyncMock(return_value=observation()),
                                  followup, audit, FakeTracer())
            task = asyncio.create_task(graph.ainvoke({}, {'configurable': {'thread_id': 'cancel-audit'}}))
            try:
                while not started.is_set(): await asyncio.sleep(0.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                followup.assert_not_awaited()
            finally:
                release.set()
        asyncio.run(cancel())


if __name__ == '__main__': unittest.main()
