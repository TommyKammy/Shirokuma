"""Advisory policy classifications without model HTTP or Kubernetes requests."""
from __future__ import annotations

import asyncio
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
import policy_contract as CONTRACT
import policy_provider as PROVIDER
import evaluate_policy as EVALUATE
import run_policy as RUN
from providers import ProviderError
from test_local_catalog_bear import AuditClient, FakeTracer, config, response


def observation():
    core = CONTRACT.CORE
    return {'schema_version': 1, 'observed_at': '2026-09-17T06:00:00.000000Z', 'authority': 'advisory_only',
        'baseline_ready': True, 'protected_namespace': 'shirokuma-dev',
        'policy_sources': {field: 'a' * 64 for field in CONTRACT.SOURCE_FIELDS},
        'rules': copy.deepcopy(core.RULES), 'boundary_rules': copy.deepcopy(core.BOUNDARY_RULES),
        'limits': list(core.LIMITS), 'scope_limit_ids': [core.OUTSIDE],
        'candidates': core.candidate_inputs('shirokuma.local/trino@sha256:' + '0' * 64),
        'boundary_candidates': core.boundary_inputs()}


class Selector:
    def __init__(self, *, incorrect=False, fail_first=False):
        self.calls = []
        self.incorrect, self.fail_first = incorrect, fail_first

    def complete(self, phase, evidence):
        self.calls.append((phase, copy.deepcopy(evidence)))
        if self.fail_first and len(self.calls) == 1:
            raise ProviderError('private-model-error', {'prompt_tokens': 123, 'completion_tokens': 45,
                'duration_ms': 12, 'content': 'private-raw-output'})
        selected = CONTRACT.expected_plan(evidence, CONTRACT.CORE.host_expectations())
        if self.incorrect:
            selected['classifications'][0]['limit_ids'] = ['B_SPENDING'] if evidence['scope'] == 'authorization-boundary' else [CONTRACT.CORE.OUTSIDE]
        return response(selected)


class ContractTests(unittest.TestCase):
    def test_fixed_corpus_is_partitioned_without_host_answers_or_raw_manifests(self):
        value = observation()
        batches = CONTRACT.batches(value)
        self.assertEqual([len(batch['candidates']) for batch in batches], [5, 5, 5, 10])
        for batch in batches:
            serialized = json.dumps(batch)
            for forbidden in ('deny_policy', 'allow-pod', 'expected', 'input_sha256', 'policy_sources', 'image":', 'manifest'):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(batch['authority'], 'advisory_only')
        self.assertEqual(len(value['candidates']), 15)
        self.assertEqual(len(value['boundary_candidates']), 10)

    def test_native_scope_control_is_not_global_approval_or_enforcement(self):
        case = next(case for case in CONTRACT.model_cases() if case['case_id'] == 'native_namespace_control')
        first, second = case['expected']['classifications']
        self.assertEqual(first, {'id': 'c10', 'violation_ids': ['P06'], 'limit_ids': []})
        self.assertEqual(second, {'id': 'c15', 'violation_ids': [], 'limit_ids': [CONTRACT.CORE.OUTSIDE]})
        self.assertEqual(case['expected']['verification_ids'], [CONTRACT.NATIVE_VERIFY])

    def test_all_eight_boundary_limits_are_classification_only(self):
        batch = CONTRACT.batches(observation())[-1]
        plan = CONTRACT.expected_plan(batch, CONTRACT.CORE.host_expectations())
        self.assertEqual([item['limit_ids'] for item in plan['classifications'][:2]], [[], []])
        self.assertEqual(len([item for item in plan['classifications'] if item['limit_ids']]), 8)
        self.assertEqual(plan['verification_ids'], [CONTRACT.BOUNDARY_VERIFY])
        self.assertTrue(all(item['violation_ids'] == [] for item in plan['classifications']))

    def test_plan_requires_exact_host_classifications_and_rejects_permission_fields(self):
        case = CONTRACT.model_cases()[1]
        expected = case['expected']
        reordered = copy.deepcopy(expected)
        reordered['classifications'].reverse()
        self.assertEqual(CONTRACT.validate_plan(reordered, case['evidence'], CONTRACT.CORE.host_expectations()), expected)
        edits = [lambda p: p.update(approved=True), lambda p: p.update(Tier='low'),
                 lambda p: p.update(manifest={'kind': 'Pod'}), lambda p: p.update(schema_version=True),
                 lambda p: p['classifications'].pop(), lambda p: p['classifications'][0].update(violation_ids=[]),
                 lambda p: p['classifications'][0].update(id='c99'),
                 lambda p: p['classifications'][0].update(limit_ids=['private-explanation']),
                 lambda p: p.update(verification_ids=[])]
        for edit in edits:
            value = copy.deepcopy(expected)
            edit(value)
            with self.subTest(edit=edit), self.assertRaises(CONTRACT.ContractError):
                CONTRACT.validate_plan(value, case['evidence'], CONTRACT.CORE.host_expectations())

    def test_observation_and_evidence_reject_unready_changed_rules_extra_objects_or_untyped_flags(self):
        edits = [lambda v: v.update(baseline_ready=False), lambda v: v.update(authority='approved'),
                 lambda v: v.update(protected_namespace='default'), lambda v: v['candidates'].pop(),
                 lambda v: v['boundary_candidates'][0].update(operation='execute'),
                 lambda v: v['candidates'][0]['features'].update(host_network=0),
                 lambda v: v['candidates'][0].update(manifest='private'),
                 lambda v: v['rules'][0].update(check='private instruction')]
        for edit in edits:
            value = observation()
            edit(value)
            with self.assertRaises(CONTRACT.ContractError):
                CONTRACT.validate_observation(value)
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.build_evidence(observation()['candidates'][:6], 'native-baseline')


class ProviderEvaluationTests(unittest.TestCase):
    def test_all_runtime_and_golden_requests_fit_limits_and_reject_before_http(self):
        profile = config()['providers']['local']
        model = PROVIDER.PolicyProvider(profile)
        batches = CONTRACT.batches(observation()) + [case['evidence'] for case in CONTRACT.model_cases()]
        for batch in batches:
            body = model.request_body('policy_classify', batch)
            self.assertLessEqual(len(json.dumps(body).encode()), 8192)
            self.assertEqual(body['max_tokens'], 512)
            self.assertIs(body['enable_thinking'], False)
        model.opener.open = Mock()
        for phase, evidence in [('other', batches[0]), ('policy_classify', {**batches[0], 'host_truth': 'private'})]:
            with self.assertRaises((ProviderError, CONTRACT.ContractError)):
                model.complete(phase, evidence)
        model.opener.open.assert_not_called()
        with self.assertRaises(ProviderError):
            PROVIDER.PolicyProvider({**profile, 'kind': 'cloud'})

    def evaluate(self, model, client, directory):
        return EVALUATE.evaluate(config(), Path(directory) / 'evidence', model=model,
                                 client=client, audit_module=EVALUATE.agenthouse_module())

    def test_eight_semantic_cases_twice_are_audited_without_authority_claims(self):
        model, client = Selector(), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
            self.assertEqual((Path(directory) / 'evidence/result.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['passed_inferences'], 16)
        self.assertEqual(report['audited_inferences'], 16)
        self.assertEqual(len(model.calls), 16)
        for event in client.events:
            self.assertEqual(event['task_id'], report['task_id'])
            self.assertEqual(event['trace_id'], report['trace_id'])
            self.assertIsNone(event['inference_cost_estimate'])

    def test_semantic_failures_and_provider_failure_keep_safe_metrics_without_retry(self):
        for model, passed in [(Selector(incorrect=True), 0), (Selector(fail_first=True), 15)]:
            with tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, AuditClient(), directory)
                serialized = '\n'.join(file.read_text() for file in (Path(directory) / 'evidence').iterdir())
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['passed_inferences'], passed)
            self.assertEqual(report['audited_inferences'], 16)
            self.assertEqual(report['usage']['prompt_tokens'], 123 * 16)
            self.assertEqual(len(model.calls), 16)
            self.assertNotIn('private-', serialized)

    def test_audit_failure_stops_remaining_classifications(self):
        for options in ({'preflight_fails': True}, {'insert_fails': True}, {'wrong_readback': True}):
            model = Selector()
            with tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, AuditClient(**options), directory)
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['audited_inferences'], 0)
            self.assertEqual(len(model.calls), 0 if options.get('preflight_fails') else 1)

    def test_interrupted_inference_is_audited_once_with_unknown_usage_and_saved_receipt(self):
        model = Mock(complete=Mock(side_effect=KeyboardInterrupt()))
        client = AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                self.evaluate(model, client, directory)
            report = json.loads((Path(directory) / 'evidence/result.json').read_text())
            self.assertEqual(report['attempted_inferences'], 1)
            self.assertEqual(report['audited_inferences'], 1)
            self.assertEqual(report['failure_code'], 'evaluation_interrupted')
            self.assertEqual(report['attempts'][0]['audit'], 'verified')
            self.assertIsNone(report['usage']['prompt_tokens'])
            self.assertEqual(client.events[0]['status'], 'failed')
        self.assertEqual(model.complete.call_count, 1)


def receipt():
    truth = CONTRACT.CORE.host_expectations()
    rows = [{'candidate_id': item['id'], 'admission': 'denied' if item['violation_ids'] else 'allowed',
             'matched_host_expectation': True, 'server_dry_run': True,
             'violation_ids': list(item['violation_ids']), 'scope_limit_ids': list(item['scope_limit_ids']),
             'input_sha256': 'b' * 64} for item in truth['candidates']]
    return {'schema_version': 1, 'observed_at': '2026-09-17T06:01:00.000000Z', 'server_dry_run': True,
        'persisted_workloads': 0, 'authority_granted': False, 'boundary_actions_executed': 0,
        'started': True, 'results': rows, 'limits': list(CONTRACT.CORE.LIMITS), 'pending': None,
        'result': 'passed', 'passed_cases': 15, 'namespace_cases': 14, 'outside_namespace_controls': 1,
        'policy_sources': observation()['policy_sources']}


class WorkflowTests(unittest.TestCase):
    def graph(self, *, observed=None, model=None, verify=None, audit=None, record_plan=None):
        observed = observation() if observed is None else observed
        model = model or Selector()
        reader = AsyncMock(return_value={'observation': observed, 'truth': CONTRACT.CORE.host_expectations()})
        verify = verify or AsyncMock(return_value=receipt())
        audit = audit or Mock()
        graph = RUN.build_graph(model, reader, verify, audit, FakeTracer(), record_plan=record_plan)
        return graph, {'configurable': {'thread_id': 'policy-test'}, 'recursion_limit': 10}, model, verify, audit

    def test_all_four_batches_match_before_fixed_full_dryrun_and_complete_advisory_checkpoint(self):
        recorded = []
        graph, options, model, verify, audit = self.graph(record_plan=recorded.append)
        state = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(len(model.calls), 4)
        self.assertEqual([len(call[1]['candidates']) for call in model.calls], [5, 5, 5, 10])
        verify.assert_awaited_once_with()
        self.assertEqual(len(recorded), 4)
        self.assertFalse(state['assessment']['authority_granted'])
        self.assertEqual(state['assessment']['server_dry_run_cases'], 15)
        self.assertEqual(state['assessment']['classified_boundary_candidates'], 10)
        self.assertEqual(graph.get_state(options).next, ())
        self.assertEqual(graph.get_state(options).values['assessment'], state['assessment'])
        self.assertEqual([call.args[:2] for call in audit.call_args_list], [('bear.policy.observe', 'success')] +
            [('llm.policy_classify', 'success')] * 4 + [('bear.policy.verify', 'success')])
        for _, evidence in model.calls:
            self.assertNotIn('truth', json.dumps(evidence))
            self.assertNotIn('deny_policy', json.dumps(evidence))

    def test_semantic_model_error_stops_later_inference_and_all_dryruns(self):
        graph, options, model, verify, audit = self.graph(model=Selector(incorrect=True))
        with self.assertRaises(RUN.PolicyBearError):
            asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(len(model.calls), 1)
        verify.assert_not_awaited()
        self.assertEqual(audit.call_args.args[:2], ('llm.policy_classify', 'blocked'))
        self.assertEqual(audit.call_args.args[2]['completion_tokens'], 45)
        self.assertNotIn('assessment', graph.get_state(options).values)

    def test_model_failure_and_plan_storage_failure_audit_known_usage_and_stop(self):
        for options_ in ({'model': Selector(fail_first=True)},
                         {'record_plan': Mock(side_effect=OSError('private-disk-error'))}):
            graph, options, model, verify, audit = self.graph(**options_)
            with self.assertRaises(RUN.PolicyBearError) as raised:
                asyncio.run(graph.ainvoke({}, options))
            self.assertNotIn('private-', str(raised.exception))
            self.assertEqual(len(model.calls), 1)
            verify.assert_not_awaited()
            self.assertEqual(audit.call_args.args[:2], ('llm.policy_classify', 'failed'))
            self.assertEqual(audit.call_args.args[2]['prompt_tokens'], 123)

    def test_audit_failure_stops_later_model_calls_and_dryrun(self):
        for failed_phase in ('bear.policy.observe', 'llm.policy_classify'):
            def record(action, *args, **kwargs):
                if action == failed_phase:
                    raise RUN.PolicyBearError('audit unavailable')
            graph, options, model, verify, _ = self.graph(audit=Mock(side_effect=record))
            with self.assertRaises(RUN.PolicyBearError):
                asyncio.run(graph.ainvoke({}, options))
            verify.assert_not_awaited()
            self.assertEqual(len(model.calls), 0 if failed_phase == 'bear.policy.observe' else 1)

    def test_inference_cancellation_records_failed_attempt_and_unknown_usage(self):
        release = threading.Event()
        async def scenario():
            entered = asyncio.Event()
            loop = asyncio.get_running_loop()
            def blocked(*args):
                loop.call_soon_threadsafe(entered.set)
                release.wait(timeout=5)
                return response(CONTRACT.expected_plan(args[1], CONTRACT.CORE.host_expectations()))
            model = Mock(complete=Mock(side_effect=blocked))
            graph, options, _, verify, audit = self.graph(model=model)
            running = asyncio.create_task(graph.ainvoke({}, options))
            await asyncio.wait_for(entered.wait(), 2)
            running.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await running
            finally:
                release.set()
            verify.assert_not_awaited()
            self.assertEqual(model.complete.call_count, 1)
            self.assertEqual(audit.call_args.args[:2], ('llm.policy_classify', 'failed'))
            self.assertIsNone(audit.call_args.args[2]['prompt_tokens'])
        asyncio.run(scenario())

    def test_unready_baseline_and_failed_or_overclaiming_receipts_cannot_complete(self):
        value = observation()
        value['baseline_ready'] = False
        graph, options, model, verify, _ = self.graph(observed=value)
        with self.assertRaises(RUN.PolicyBearError):
            asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(len(model.calls), 0)
        verify.assert_not_awaited()
        edits = [lambda r: r.update(persisted_workloads=1), lambda r: r.update(authority_granted=True),
                 lambda r: r.update(boundary_actions_executed=1), lambda r: r['results'].pop(),
                 lambda r: r.update(pending={'candidate_id': 'c15'}), lambda r: r['results'][2].update(admission='allowed'),
                 lambda r: r['policy_sources'].update(native_manifest='c' * 64)]
        for edit in edits:
            value = receipt()
            edit(value)
            with self.assertRaises(CONTRACT.ContractError):
                CONTRACT.verify_receipt(value, observation(), CONTRACT.CORE.host_expectations())
        failed = AsyncMock(side_effect=RuntimeError('private-server-error'))
        graph, options, _, verify, audit = self.graph(verify=failed)
        with self.assertRaises(RUN.PolicyBearError):
            asyncio.run(graph.ainvoke({}, options))
        verify.assert_awaited_once_with()
        self.assertEqual(audit.call_args.args[:2], ('bear.policy.verify', 'failed'))
        self.assertNotIn('assessment', graph.get_state(options).values)

    def run_fake(self, output, *, fail_dryrun=False):
        audit_client, agenthouse = AuditClient(), EVALUATE.agenthouse_module()
        loki, logs, observers = Mock(), [], []
        loki.push_record.side_effect = lambda value: logs.append(copy.deepcopy(value))
        loki.query_task.side_effect = lambda task: [row for row in logs if row['task_id'] == task]

        class Observer:
            def __init__(self):
                self.verify_calls = 0
                self.current = {**receipt(), 'started': False, 'result': 'not-started', 'results': []}
                observers.append(self)
            def observe(self):
                return observation()
            def host_truth(self):
                return CONTRACT.CORE.host_expectations()
            def receipt(self):
                return copy.deepcopy(self.current)
            def verify(self, *, progress):
                self.verify_calls += 1
                self.current = {**receipt(), 'result': 'running', 'pending': {'candidate_id': 'c03',
                    'input_sha256': 'b' * 64, 'outcome': 'unknown'}, 'results': receipt()['results'][:2]}
                progress(self.receipt())
                if fail_dryrun:
                    self.current['result'] = 'failed'
                    raise RuntimeError('private-kube-error')
                self.current = receipt()
                progress(self.receipt())
                return self.receipt()

        with patch.object(RUN, 'PolicyProvider', return_value=Selector()), \
                patch.object(RUN, 'GatekeeperObserver', Observer), \
                patch.object(RUN, 'agenthouse_module', return_value=agenthouse), \
                patch.object(agenthouse, 'Client', return_value=audit_client), patch.object(RUN, 'module', return_value=loki):
            report = asyncio.run(RUN.run(config(), output))
        return report, observers

    def test_runner_verifies_seven_phase_audits_spans_and_durable_private_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            report, observers = self.run_fake(output)
            self.assertEqual(report['result'], 'passed')
            self.assertEqual(report['correlated_events'], 7)
            self.assertEqual(report['saved_otel_spans_verified'], 7)
            self.assertEqual(report['checkpoint'], 'completed-in-memory')
            self.assertEqual(len(report['classified_batches']), 4)
            self.assertEqual(observers[0].verify_calls, 1)
            for filename in ('result.json', 'plans.jsonl', 'spans.jsonl', 'verification-progress.json'):
                self.assertEqual((output / filename).stat().st_mode & 0o777, 0o600)
            progress = json.loads((output / 'verification-progress.json').read_text())
            self.assertEqual(progress['result'], 'passed')
            self.assertEqual(len(progress['results']), 15)
            self.assertIsNone(progress['pending'])

    def test_partial_dryrun_preserves_pending_and_prior_results_without_completion_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            report, observers = self.run_fake(output, fail_dryrun=True)
            progress = json.loads((output / 'verification-progress.json').read_text())
            self.assertEqual(report['result'], 'failed')
            self.assertNotIn('checkpoint', report)
            self.assertEqual(progress['pending']['candidate_id'], 'c03')
            self.assertEqual(len(progress['results']), 2)
            self.assertEqual(len(report['classified_batches']), 4)
            self.assertEqual(observers[0].verify_calls, 1)
            self.assertNotIn('private-', (output / 'result.json').read_text())


if __name__ == '__main__':
    unittest.main()
