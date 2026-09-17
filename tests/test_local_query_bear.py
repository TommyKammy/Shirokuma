"""Fixed-query observations and model selection without Trino or model HTTP calls."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
import query_contract as CONTRACT
import query_provider as PROVIDER
import evaluate_query as EVALUATE
import run_query as RUN
from providers import ProviderError
from test_local_catalog_bear import AuditClient, FakeTracer, config, response


def observation():
    cases, entries = [], []
    stamp = '2026-09-17T06:00:00.000000Z'
    for index, (case_id, count) in enumerate(zip(CONTRACT.CASES, (2, 1))):
        ids = {kind: f'query_{index}_{kind}' for kind in ('select', 'explain')}
        nodes = [{'id': f'p{node_index:02}', 'kind': kind, 'columns': [{'name': 'id', 'type': 'bigint'}],
                  'filter_present': kind == 'ScanFilter',
                  'estimates': [{field: 0 for field in CONTRACT.ESTIMATE_FIELDS}]}
                 for node_index, kind in enumerate(('Output', 'TableScan' if index == 0 else 'ScanFilter'), 1)]
        cases.append({'id': case_id, 'select': {'query_id': ids['select'], 'row_count': count, 'matched': True},
            'explain': {'query_id': ids['explain'], 'plan': {'format': 'trino-483-distributed-json',
                'fragment_count': 1, 'nodes': nodes, 'estimates_complete': True}}})
        for kind, query_id in ids.items():
            entries.append({'query_id': query_id, 'case_id': case_id, 'kind': kind, 'state': 'FINISHED',
                'created_at': stamp, 'started_at': stamp, 'ended_at': stamp,
                **{field: 0 for field in CONTRACT.TIMING_FIELDS}, 'error_type': None, 'error_code': None})
    return {'schema_version': 1, 'run_token': 'a' * 32, 'observed_at': stamp, 'cases': cases,
        'history': {'query_id': 'query_history', 'observed_at': stamp, 'completeness': 'complete',
                    'missing_query_ids': [], 'entries': entries, 'durations_complete': True},
        'performance_gain': 'unmeasured'}


def unknown_observation():
    value = observation()
    value['cases'][1]['explain']['plan']['nodes'][0]['estimates'][0]['rows'] = None
    value['cases'][1]['explain']['plan']['estimates_complete'] = False
    value['history']['entries'][0]['queued_ms'] = None
    return value


def stats():
    return {'query_id': 'query_stats', 'observed_at': '2026-09-17T06:00:01.000000Z',
        'table': 'fixture_v1', 'row_count_estimate': 2.0, 'columns': [
            {'name': name, 'data_size': None, 'distinct_values_count': None, 'nulls_fraction': None}
            for name in ('id', 'name')], 'estimates_complete': False}


class Selector:
    def __init__(self, *, incorrect=False, fail_first=False):
        self.calls = []
        self.incorrect, self.fail_first = incorrect, fail_first

    def complete(self, phase, evidence):
        self.calls.append((phase, copy.deepcopy(evidence)))
        if self.fail_first and len(self.calls) == 1:
            raise ProviderError('private-model-error', {'prompt_tokens': 123, 'completion_tokens': 45,
                'duration_ms': 12, 'content': 'private-raw-content'})
        selected = CONTRACT.expected_plan(evidence)
        if self.incorrect:
            selected['root_cause'] = 'private-diagnosis'
        return response(selected)


class ContractTests(unittest.TestCase):
    def test_zeros_are_known_and_do_not_trigger_followups(self):
        evidence = CONTRACT.project_observation(observation())
        self.assertEqual(evidence['findings'], [])
        self.assertEqual([case['estimate_zero_count'] for case in evidence['cases']], [10, 10])
        self.assertEqual(evidence['history']['timing_zero_count'], 20)
        self.assertEqual(CONTRACT.expected_plan(evidence)['checks'], [])

    def test_missing_estimate_node_and_nullable_phase_timing_are_preserved(self):
        value = observation()
        plan = value['cases'][0]['explain']['plan']
        plan['nodes'][0]['estimates'] = []
        plan['estimates_complete'] = False
        value['history']['entries'][0]['queued_ms'] = None
        evidence = CONTRACT.project_observation(value)
        self.assertEqual(evidence['cases'][0]['estimate_slots'], 1)
        self.assertEqual(evidence['cases'][0]['estimate_empty_nodes'], 1)
        self.assertEqual(evidence['cases'][0]['estimate_unknown_count'], 0)
        self.assertTrue(evidence['history']['durations_complete'])
        self.assertEqual(evidence['history']['timing_unknown_count'], 1)
        self.assertEqual([check['tool'] for check in CONTRACT.expected_plan(evidence)['checks']], list(CONTRACT.TOOLS))

    def test_history_foreign_identity_duration_mismatch_and_raw_fields_reject(self):
        edits = [lambda v: v['history']['entries'][0].update(query_id='foreign'),
                 lambda v: v['history']['entries'][0].update(created_to_end_ms=1),
                 lambda v: v['history']['entries'][0].update(state='RUNNING'),
                 lambda v: v['cases'][0]['select'].update(matched=False),
                 lambda v: v['cases'][0]['explain']['plan']['nodes'][0]['estimates'][0].update(rows=float('nan')),
                 lambda v: v.update(raw_sql='private sql')]
        for edit in edits:
            value = observation()
            edit(value)
            with self.subTest(edit=edit), self.assertRaises(CONTRACT.ContractError):
                CONTRACT.project_observation(value)

    def test_plan_requires_exact_all_findings_without_sql_causes_or_improvement(self):
        evidence = CONTRACT.project_observation(unknown_observation())
        expected = CONTRACT.expected_plan(evidence)
        reversed_plan = copy.deepcopy(expected)
        reversed_plan['checks'].reverse()
        self.assertEqual(CONTRACT.validate_plan(reversed_plan, evidence), expected)
        edits = [lambda p: p['checks'].pop(), lambda p: p['checks'].append(p['checks'][0]),
                 lambda p: p['checks'][0].update(sql='private sql'),
                 lambda p: p['checks'][0]['finding_ids'].clear(), lambda p: p.update(root_cause='slow scan'),
                 lambda p: p.update(performance_gain='faster'), lambda p: p.update(schema_version=True)]
        for edit in edits:
            value = copy.deepcopy(expected)
            edit(value)
            with self.assertRaises(CONTRACT.ContractError):
                CONTRACT.validate_plan(value, evidence)

    def test_followup_unknown_stats_remain_unknown_and_cannot_claim_gain(self):
        value = unknown_observation()
        plan = CONTRACT.expected_plan(CONTRACT.project_observation(value))
        followups = {'fixture_stats': stats(), 'own_query_history': copy.deepcopy(value['history'])}
        assessed = CONTRACT.assess_followups(value, plan, followups)
        self.assertEqual(assessed['followups']['fixture_stats']['unknown_count'], 6)
        self.assertEqual(assessed['performance_gain'], 'unmeasured')
        self.assertEqual(assessed['root_cause'], 'undetermined')
        followups['fixture_stats']['observed_at'] = '2026-09-16T06:00:00.000000Z'
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.assess_followups(value, plan, followups)


class ProviderEvaluationTests(unittest.TestCase):
    def test_provider_validates_before_http_and_keeps_optional_thinking_and_cloud_boundary(self):
        profile = config()['providers']['local']
        model = PROVIDER.QueryProvider(profile)
        evidence = CONTRACT.model_cases()[0]['evidence']
        body = model.request_body('query_plan', evidence)
        self.assertIs(body['enable_thinking'], False)
        self.assertLessEqual(len(json.dumps(body).encode()), 8192)
        self.assertIn('not measured execution metrics', body['messages'][0]['content'])
        del profile['enable_thinking']
        self.assertNotIn('enable_thinking', PROVIDER.QueryProvider(profile).request_body('query_plan', evidence))
        model.opener.open = Mock()
        for phase, value in [('unknown', evidence), ('query_plan', {**evidence, 'raw': 'private'})]:
            with self.assertRaises((ProviderError, CONTRACT.ContractError)):
                model.complete(phase, value)
        model.opener.open.assert_not_called()
        with self.assertRaises(ProviderError):
            PROVIDER.QueryProvider({**profile, 'kind': 'cloud'})

    def evaluate(self, model, client, directory):
        return EVALUATE.evaluate(config(), Path(directory) / 'evidence', model=model,
                                 client=client, audit_module=EVALUATE.agenthouse_module())

    def test_eight_golden_cases_twice_and_correlated_private_receipts(self):
        model, client = Selector(), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
            self.assertEqual((Path(directory) / 'evidence/result.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((Path(directory) / 'evidence/result.json').read_text()), report)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['passed_inferences'], 16)
        self.assertEqual(report['audited_inferences'], 16)
        self.assertEqual(len(model.calls), 16)
        self.assertEqual(report['usage']['completion_tokens'], 45 * 16)
        for event in client.events:
            self.assertEqual(event['task_id'], report['task_id'])
            self.assertEqual(event['trace_id'], report['trace_id'])
            self.assertIsNone(event['inference_cost_estimate'])

    def test_invalid_plans_and_failed_usage_are_retained_without_retry_or_raw_output(self):
        for model, passed in [(Selector(incorrect=True), 0), (Selector(fail_first=True), 15)]:
            with tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, AuditClient(), directory)
                raw = '\n'.join(file.read_text() for file in (Path(directory) / 'evidence').iterdir())
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['passed_inferences'], passed)
            self.assertEqual(report['audited_inferences'], 16)
            self.assertEqual(len(model.calls), 16)
            self.assertEqual(report['usage']['prompt_tokens'], 123 * 16)
            self.assertNotIn('private-', raw)

    def test_audit_failures_stop_later_inferences(self):
        for options in ({'preflight_fails': True}, {'insert_fails': True}, {'wrong_readback': True}):
            model = Selector()
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, AuditClient(**options), directory)
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['audited_inferences'], 0)
            self.assertEqual(len(model.calls), 0 if options.get('preflight_fails') else 1)


class WorkflowTests(unittest.TestCase):
    def graph(self, *, observed=None, model=None, reader=None, audit=None):
        observed = unknown_observation() if observed is None else observed
        model = model or Selector()
        history = copy.deepcopy(observed['history'])
        history['observed_at'] = '2026-09-17T06:00:01.000000Z'
        reader = reader or AsyncMock(side_effect=lambda tool: stats() if tool == 'fixture_stats' else history)
        audit = audit or Mock()
        graph = RUN.build_graph(model, AsyncMock(return_value=observed), reader, audit, FakeTracer())
        return graph, {'configurable': {'thread_id': 'query-test'}, 'recursion_limit': 10}, model, reader, audit

    def test_valid_plan_performs_exact_two_reads_and_completes_checkpoint_without_diagnosis(self):
        graph, options, model, read, audit = self.graph()
        state = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual([call.args for call in read.await_args_list], [('fixture_stats',), ('own_query_history',)])
        self.assertEqual(state['assessment']['performance_gain'], 'unmeasured')
        self.assertEqual(state['assessment']['root_cause'], 'undetermined')
        self.assertEqual(graph.get_state(options).next, ())
        self.assertEqual(graph.get_state(options).values['assessment'], state['assessment'])
        self.assertEqual([call.args[:2] for call in audit.call_args_list], [
            ('bear.query.observe', 'success'), ('llm.query_plan', 'success'),
            ('bear.query.fixture_stats', 'success'), ('bear.query.own_query_history', 'success')])
        sent = json.dumps(model.calls[0][1])
        self.assertNotIn('query_0_select', sent)
        self.assertNotIn('2026-', sent)
        self.assertNotIn('raw', sent)

    def test_complete_numeric_observations_require_no_extra_read(self):
        graph, options, _, read, _ = self.graph(observed=observation())
        state = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(state['plan']['checks'], [])
        self.assertEqual(state['assessment']['followups'], {})
        read.assert_not_awaited()

    def test_invalid_model_plan_blocks_reads_and_retains_usage(self):
        graph, options, _, read, audit = self.graph(model=Selector(incorrect=True))
        with self.assertRaises(RUN.QueryError):
            asyncio.run(graph.ainvoke({}, options))
        read.assert_not_awaited()
        self.assertEqual(audit.call_args.args[:2], ('llm.query_plan', 'blocked'))
        self.assertEqual(audit.call_args.args[2]['completion_tokens'], 45)
        self.assertNotIn('assessment', graph.get_state(options).values)

    def test_model_failure_audits_safe_usage_and_does_not_read_or_retry(self):
        model = Selector(fail_first=True)
        graph, options, _, read, audit = self.graph(model=model)
        with self.assertRaises(RUN.QueryError) as error:
            asyncio.run(graph.ainvoke({}, options))
        self.assertNotIn('private-', str(error.exception))
        read.assert_not_awaited()
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(audit.call_args.args[:2], ('llm.query_plan', 'failed'))
        self.assertEqual(audit.call_args.args[2]['prompt_tokens'], 123)

    def test_cancelled_inference_records_unknown_usage_and_does_not_start_followup(self):
        release = threading.Event()
        async def scenario():
            started = asyncio.Event()
            loop = asyncio.get_running_loop()
            def blocked_model(*args):
                loop.call_soon_threadsafe(started.set)
                release.wait(timeout=5)
                return response(CONTRACT.expected_plan(args[1]))
            model = Mock(complete=Mock(side_effect=blocked_model))
            graph, options, _, read, audit = self.graph(model=model)
            running = asyncio.create_task(graph.ainvoke({}, options))
            await asyncio.wait_for(started.wait(), 2)
            running.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await running
            finally:
                release.set()
            read.assert_not_awaited()
            calls = [call for call in audit.call_args_list if call.args[0] == 'llm.query_plan']
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args[1], 'failed')
            self.assertIsNone(calls[0].args[2]['prompt_tokens'])
            self.assertIsNone(calls[0].args[2]['completion_tokens'])
            self.assertNotIn('assessment', graph.get_state(options).values)
        asyncio.run(scenario())

    def test_audit_failure_stops_before_inference_or_next_read(self):
        for failed_phase, expected_calls, expected_reads in [('bear.query.observe', 0, 0),
                ('llm.query_plan', 1, 0), ('bear.query.fixture_stats', 1, 1)]:
            def record(action, *args, **kwargs):
                if action == failed_phase:
                    raise RUN.QueryError('audit unavailable')
            graph, options, model, read, _ = self.graph(audit=Mock(side_effect=record))
            with self.subTest(phase=failed_phase), self.assertRaises(RUN.QueryError):
                asyncio.run(graph.ainvoke({}, options))
            self.assertEqual(len(model.calls), expected_calls)
            self.assertEqual(read.await_count, expected_reads)

    def test_failed_or_invalid_followup_stops_second_read_and_audits_elapsed_time(self):
        async def failing(tool):
            await asyncio.sleep(0.01)
            raise RuntimeError('private-trino-error')
        for reader in (AsyncMock(side_effect=failing), AsyncMock(return_value={**stats(), 'table': 'other'})):
            graph, options, _, read, audit = self.graph(reader=reader)
            with self.assertRaises(RUN.QueryError) as error:
                asyncio.run(graph.ainvoke({}, options))
            self.assertNotIn('private-', str(error.exception))
            self.assertEqual(read.await_count, 1)
            self.assertEqual(audit.call_args.args[:2], ('bear.query.fixture_stats', 'failed'))
            if reader.side_effect is failing:
                self.assertGreaterEqual(audit.call_args.args[2]['duration_ms'], 5)

    def test_invalid_observation_rejects_before_model_and_cancellation_is_preserved(self):
        bad = observation()
        bad['cases'][0]['select']['matched'] = False
        graph, options, model, read, audit = self.graph(observed=bad)
        with self.assertRaises(RUN.QueryError):
            asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(len(model.calls), 0)
        read.assert_not_awaited()
        self.assertEqual(audit.call_args.args[:2], ('bear.query.observe', 'failed'))
        async def cancellation():
            entered = asyncio.Event()
            async def observe():
                entered.set()
                await asyncio.Event().wait()
            audit = Mock()
            graph = RUN.build_graph(Mock(), observe, AsyncMock(), audit, FakeTracer())
            running = asyncio.create_task(graph.ainvoke({}, {'configurable': {'thread_id': 'cancel'}}))
            await entered.wait()
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
            self.assertEqual(audit.call_args.args[:2], ('bear.query.observe', 'failed'))
        asyncio.run(cancellation())

    def run_fake(self, output, *, observer_failure=False, incomplete=False):
        audit_client, agenthouse = AuditClient(), EVALUATE.agenthouse_module()
        loki, logs, observers, lifecycle = Mock(), [], [], []
        loki.push_record.side_effect = lambda value: logs.append(copy.deepcopy(value))
        loki.query_task.side_effect = lambda task: [row for row in logs if row['task_id'] == task]

        class Observer:
            def __init__(self, url, run_token):
                self.token = run_token
                self.calls = []
                observers.append(self)
            def collect(self):
                self.calls.append('collect')
                if observer_failure:
                    raise RuntimeError('private-trino-error')
                value = unknown_observation()
                value['run_token'] = self.token
                return value
            def followup(self, tool):
                self.calls.append(tool)
                return stats() if tool == 'fixture_stats' else unknown_observation()['history']
            def receipt(self):
                lifecycle.append('receipt')
                return {'schema_version': 1, 'run_token': self.token,
                    'outcome': 'incomplete' if incomplete or observer_failure else 'completed',
                    'cleanup_verified': not (incomplete or observer_failure),
                    'target_history_verified': not observer_failure, 'server_max_run_seconds': 30,
                    'requests': [], 'uncaptured_requests': 1 if observer_failure else 0}

        @contextlib.contextmanager
        def tunnel():
            lifecycle.append('tunnel-enter')
            yield 'http://127.0.0.1:9999'
            lifecycle.append('tunnel-exit')

        with patch.object(RUN, 'QueryProvider', return_value=Selector()), \
                patch.object(RUN, 'QueryObserver', Observer), patch.object(RUN.SESSION, 'check_context'), \
                patch.object(RUN.SESSION, 'trino_tunnel', tunnel), \
                patch.object(RUN, 'agenthouse_module', return_value=agenthouse), \
                patch.object(agenthouse, 'Client', return_value=audit_client), patch.object(RUN, 'module', return_value=loki):
            report = asyncio.run(RUN.run(config(), output))
        return report, observers, lifecycle

    def test_runner_verifies_dynamic_audits_saved_spans_and_private_observer_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            report, observers, lifecycle = self.run_fake(output)
            self.assertEqual(report['result'], 'passed')
            self.assertEqual(report['correlated_events'], 5)
            self.assertEqual(report['saved_otel_spans_verified'], 5)
            self.assertEqual(report['checkpoint'], 'completed-in-memory')
            self.assertEqual(observers[0].calls, ['collect', 'fixture_stats', 'own_query_history'])
            self.assertEqual(lifecycle[-2:], ['receipt', 'tunnel-exit'])
            for filename in ('result.json', 'spans.jsonl', 'observer-receipt.json', 'model-evidence.json'):
                self.assertEqual((output / filename).stat().st_mode & 0o777, 0o600)
            receipt = json.loads((output / 'observer-receipt.json').read_text())
            self.assertTrue(receipt['cleanup_verified'])
            self.assertEqual(receipt['run_token'], report['query_run_token'])

    def test_runner_keeps_partial_receipt_and_does_not_claim_completion(self):
        for options in ({'observer_failure': True}, {'incomplete': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'evidence'
                report, _, _ = self.run_fake(output, **options)
                receipt = json.loads((output / 'observer-receipt.json').read_text())
                self.assertFalse(receipt['cleanup_verified'])
                self.assertEqual(report['result'], 'failed')
                self.assertNotIn('checkpoint', report)
                self.assertNotIn('private-', (output / 'result.json').read_text())


if __name__ == '__main__':
    unittest.main()
