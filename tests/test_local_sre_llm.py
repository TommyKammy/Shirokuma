from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
import run_sre_llm as RUN
import sre_contract as CONTRACT
import sre_provider as PROVIDER
import evaluate_sre as EVALUATION
from providers import ProviderError

TASK_ID, TRACE_ID = 'workflow-' + 'a' * 32, 'b' * 32


def local_profile(**updates):
    config = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
    value = {**config['providers']['local'], 'enabled': True, 'model': 'qwen3.8-flash-next',
             'max_output_tokens': 512}
    value.update(updates)
    return value


def local_config():
    value = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
    value['active_provider'] = 'local'
    value['providers']['local'] = local_profile()
    return value


def snapshots(*, critical=True):
    stamp = '2026-09-17T02:00:00.000000Z'
    return {
        'shirokuma_doctor': {'profile': 'local-experiment', 'context': 'colima-mac-studio-solo',
            'observed_at': stamp, 'checks': [{'name': 'storage', 'status': 'healthy', 'summary': 'private-doctor-detail'}]},
        'k8s_read_pods': {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False,
            'pods': [{'name': 'trino', 'uid': 'pod-1', 'phase': 'Running',
                      'ready_containers': 0 if critical else 1, 'total_containers': 1, 'restarts': 0}]},
        'k8s_read_events': {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False,
            'events': [{'uid': 'event-1', 'object_uid': 'pod-1', 'object_name': 'trino', 'object_kind': 'Pod',
                        'last_observed_at': stamp, 'count': 1, 'type': 'Warning', 'reason': 'Unhealthy',
                        'message': 'private-event-detail'}] if critical else []},
    }


def evidence_from(value):
    return CONTRACT.build_evidence(*(value[tool] for tool in RUN.TOOLS))


def valid_triage(evidence):
    checks = []
    critical_ids = [finding['id'] for finding in evidence['findings'] if finding['severity'] == 'critical']
    if critical_ids:
        checks.append({'tool': 'k8s_read_pods', 'finding_ids': critical_ids})
    warning_ids = [finding['id'] for finding in evidence['findings'] if finding['code'] == 'WARNING_EVENT']
    if warning_ids:
        checks.append({'tool': 'k8s_read_events', 'finding_ids': warning_ids})
    return {'assessment': evidence['health'], 'checks': checks, 'root_cause': 'undetermined'}


def inference(content):
    return {'content': content, 'prompt_tokens': 123, 'completion_tokens': 45,
            'duration_ms': 12, 'inference_cost_estimate': None}


class FakeTracer:
    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name, **options):
        span = Mock()
        self.spans.append((name, options, span))
        return contextlib.nullcontext(span)


def gateway_response(**updates):
    payload = {'status': 'success', 'audit': 'delivered', 'task_id': TASK_ID, 'trace_id': TRACE_ID,
               'span_id': 'c' * 16, 'event_id': '11111111-1111-1111-1111-111111111111', 'data': {'pods': []}}
    payload.update(updates)
    return SimpleNamespace(structured_content=payload, is_error=False)


def audit_fixture():
    call = {'event_id': '11111111-1111-1111-1111-111111111111', 'span_id': '1' * 16,
            'action_type': 'mcp.k8s_read_pods'}
    record = {'event_id': '22222222-2222-2222-2222-222222222222', 'span_id': '2' * 16,
              'action_type': 'llm.sre_triage', 'model_provider': 'local-compatible',
              'model_name': 'qwen3.8-flash-next', 'prompt_tokens': 123, 'completion_tokens': 45,
              'inference_cost_estimate': None, 'duration_ms': 1}
    rows, logs = [], []
    for source in (call, record):
        rows.append({'event_id': source['event_id'], 'task_id': TASK_ID, 'trace_id': TRACE_ID,
            'action_type': source['action_type'], 'status': 'success',
            'model_provider': source.get('model_provider', 'none'), 'model_name': source.get('model_name', ''),
            'prompt_tokens': str(source['prompt_tokens']) if 'prompt_tokens' in source else None,
            'completion_tokens': str(source['completion_tokens']) if 'completion_tokens' in source else None,
            'inference_cost_estimate': None, 'duration_ms': str(source.get('duration_ms', 0))})
        logs.append({'task_id': TASK_ID, 'trace_id': TRACE_ID, 'span_id': source['span_id'],
                     'action': source['action_type'], 'status': 'success'})
    return rows, logs, [call], [record]


class FixtureSelector:
    def __init__(self, *, omit_critical=False, fail_first=False):
        self.calls = []
        self.omit_critical = omit_critical
        self.fail_first = fail_first

    def complete(self, phase, evidence):
        self.calls.append((phase, copy.deepcopy(evidence)))
        if self.fail_first and len(self.calls) == 1:
            raise ProviderError('private-model-error', {'prompt_tokens': 123, 'completion_tokens': 45,
                                                        'duration_ms': 12, 'content': 'private-output'})
        selected = {}
        for finding in evidence['findings']:
            if self.omit_critical and finding['severity'] == 'critical':
                continue
            tool = 'k8s_read_pods' if finding['severity'] == 'critical' else finding['allowed_tools'][0]
            selected.setdefault(tool, []).append(finding['id'])
        return inference({'assessment': evidence['health'], 'root_cause': 'undetermined',
                          'checks': [{'tool': tool, 'finding_ids': ids} for tool, ids in selected.items()]})


class EvaluationClient:
    def __init__(self, *, preflight_fails=False, insert_fails=False):
        self.events = []
        self.preflight_fails = preflight_fails
        self.insert_fails = insert_fails

    def query(self, sql):
        if sql == 'SELECT 1':
            if self.preflight_fails:
                raise RuntimeError('private-audit-preflight')
            return '1\n'
        if sql.startswith('INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n'):
            if self.insert_fails:
                raise RuntimeError('private-audit-insert')
            self.events.append(json.loads(sql.split('\n', 1)[1]))
            return ''
        matched = re.search(r"WHERE event_id = '([a-f0-9-]{36})' FORMAT JSON$", sql)
        if not matched:
            raise AssertionError('unexpected evaluation query')
        rows = [copy.deepcopy(row) for row in self.events if row['event_id'] == matched.group(1)]
        for row in rows:
            for key in ('prompt_tokens', 'completion_tokens', 'duration_ms'):
                if row[key] is not None:
                    row[key] = str(row[key])
        return json.dumps({'data': rows})


class SRELLMTests(unittest.TestCase):
    def test_provider_request_has_only_bounded_projection_and_optional_thinking_control(self):
        evidence = evidence_from(snapshots())
        for flag in (None, False, True):
            profile = local_profile(**({} if flag is None else {'enable_thinking': flag}))
            model = PROVIDER.SREProvider(profile)
            body = model.request_body('sre_triage', evidence)
            self.assertEqual(body['model'], profile['model'])
            self.assertEqual(body['max_tokens'], 512)
            self.assertFalse(body['stream'])
            self.assertEqual(json.loads(body['messages'][-1]['content']), {'phase': 'sre_triage', 'evidence': evidence})
            self.assertNotIn('private-', json.dumps(body))
            self.assertLessEqual(len(json.dumps(body).encode()), 8192)
            if flag is None:
                self.assertNotIn('enable_thinking', body)
            else:
                self.assertIs(body['enable_thinking'], flag)

    def test_provider_rejects_unknown_phase_or_extra_evidence_before_http(self):
        model = PROVIDER.SREProvider(local_profile())
        model.opener.open = Mock()
        for phase, evidence in (('plan', evidence_from(snapshots())), ('sre_triage', None),
                                ('sre_triage', {**evidence_from(snapshots()), 'raw_message': 'private'})):
            with self.subTest(phase=phase), self.assertRaises((ProviderError, ValueError)):
                model.complete(phase, evidence)
        model.opener.open.assert_not_called()

    def test_valid_large_evidence_still_cannot_exceed_eight_kib_request(self):
        value = snapshots()
        value['k8s_read_events']['events'] = []
        pod = value['k8s_read_pods']['pods'][0]
        value['k8s_read_pods']['pods'] = [{**pod, 'name': f'pod_{index:02d}_' + 'x' * 121,
                                          'uid': f'pod-{index}'} for index in range(24)]
        evidence = evidence_from(value)
        self.assertEqual(len(evidence['findings']), 24)
        model = PROVIDER.SREProvider(local_profile())
        model.opener.open = Mock()
        with self.assertRaises(ProviderError):
            model.complete('sre_triage', evidence)
        model.opener.open.assert_not_called()

    def test_cloud_provider_cannot_execute_sre_inference(self):
        with self.assertRaises(ProviderError):
            PROVIDER.SREProvider({**local_profile(), 'kind': 'cloud'})

    def test_observation_accepts_only_successful_audited_correlated_receipt(self):
        response = gateway_response()
        data, receipt = RUN.observation(response, 'k8s_read_pods', TASK_ID, TRACE_ID)
        self.assertEqual(data, {'pods': []})
        self.assertEqual(receipt['event_id'], response.structured_content['event_id'])
        self.assertEqual(receipt['action_type'], 'mcp.k8s_read_pods')
        invalid = [gateway_response(status='failed'), gateway_response(audit='failed'),
                   gateway_response(task_id='different'), gateway_response(trace_id='0' * 32),
                   gateway_response(span_id='invalid'), gateway_response(span_id='1' * 17),
                   gateway_response(data=[]), gateway_response(event_id='invalid')]
        error_response = gateway_response()
        error_response.is_error = True
        invalid.extend([error_response, SimpleNamespace(structured_content=None, is_error=False)])
        for response in invalid:
            with self.subTest(response=response), self.assertRaises((RUN.SREError, ValueError, KeyError)):
                RUN.observation(response, 'k8s_read_pods', TASK_ID, TRACE_ID)

    def test_retained_audits_require_matching_clickhouse_and_loki_records(self):
        rows, logs, calls, records = audit_fixture()
        RUN.verify_audits(rows, logs, calls, records, TASK_ID, TRACE_ID)
        for field, value in (('event_id', '33333333-3333-3333-3333-333333333333'),
                             ('task_id', 'wrong-task'), ('trace_id', 'wrong-trace'), ('status', 'failed'),
                             ('action_type', 'other'), ('model_name', 'other-model'),
                             ('prompt_tokens', '124'), ('completion_tokens', True),
                             ('inference_cost_estimate', 0), ('duration_ms', '2')):
            changed = copy.deepcopy(rows)
            changed[1][field] = value
            with self.subTest(field=field), self.assertRaises(RUN.SREError):
                RUN.verify_audits(changed, logs, calls, records, TASK_ID, TRACE_ID)
        for changed_rows in (rows[:1], rows + [rows[0]], [rows[0], rows[0]]):
            with self.assertRaises(RUN.SREError):
                RUN.verify_audits(changed_rows, logs, calls, records, TASK_ID, TRACE_ID)
        with self.assertRaises(RUN.SREError):
            RUN.verify_audits(rows, logs, calls + calls, records, TASK_ID, TRACE_ID)
        for field in ('task_id', 'trace_id', 'span_id', 'action', 'status'):
            changed_logs = copy.deepcopy(logs)
            changed_logs[1][field] = 'different'
            with self.subTest(log_field=field), self.assertRaises(RUN.SREError):
                RUN.verify_audits(rows, changed_logs, calls, records, TASK_ID, TRACE_ID)

    def test_retained_duration_never_coerces_bool_fraction_negative_or_overflow(self):
        rows, logs, calls, records = audit_fixture()
        for value in (True, 1.9, -1, 2**64, '1.0', '+1', '１'):
            changed = copy.deepcopy(rows)
            changed[1]['duration_ms'] = value
            with self.subTest(duration=value), self.assertRaises(RUN.SREError):
                RUN.verify_audits(changed, logs, calls, records, TASK_ID, TRACE_ID)

    def run_graph(self, model, *, before=None, after=None, audit=None):
        before = before or snapshots()
        after = after or copy.deepcopy(before)
        async def read_snapshot(tool, phase):
            return copy.deepcopy((before if phase == 'initial' else after)[tool])
        read = AsyncMock(side_effect=read_snapshot)
        audit = audit or Mock()
        tracer = FakeTracer()
        graph = RUN.build_graph(model, read, audit, tracer)
        options = {'configurable': {'thread_id': 'test-sre'}, 'recursion_limit': 8}
        return graph, options, read, audit, tracer

    def test_valid_critical_selection_reobserves_only_selected_sources_and_completes_checkpoint(self):
        before = snapshots()
        after = copy.deepcopy(before)
        for snapshot in after.values():
            snapshot['observed_at'] = '2026-09-17T02:00:01.000000Z'
        after['k8s_read_pods']['pods'][0]['ready_containers'] = 1
        after['k8s_read_events']['events'][0]['count'] = 2
        after['k8s_read_events']['events'][0]['last_observed_at'] = '2026-09-17T02:00:01.000000Z'
        model = Mock()
        model.complete.return_value = inference(valid_triage(evidence_from(before)))
        graph, options, read, audit, tracer = self.run_graph(model, before=before, after=after)
        result = asyncio.run(graph.ainvoke({}, options))
        checkpoint = graph.get_state(options)
        self.assertFalse(checkpoint.next)
        self.assertEqual(checkpoint.values['comparisons'], result['comparisons'])
        self.assertEqual([call.args for call in read.await_args_list],
                         [(tool, 'initial') for tool in RUN.TOOLS]
                         + [('k8s_read_pods', 'followup'), ('k8s_read_events', 'followup')])
        self.assertEqual(result['comparisons']['root_cause'], 'undetermined')
        self.assertEqual(result['comparisons']['sources']['shirokuma_doctor']['status'], 'not-reobserved')
        self.assertEqual(result['comparisons']['sources']['k8s_read_pods']['status'], 'changed')
        self.assertEqual(result['comparisons']['sources']['k8s_read_events']['status'], 'changed')
        self.assertEqual(set(result['followup_evidence']), {'k8s_read_pods', 'k8s_read_events'})
        for value in result['followup_evidence'].values():
            self.assertRegex(value, r'^[0-9a-f]{64}$')
        model.complete.assert_called_once_with('sre_triage', result['evidence'])
        self.assertNotIn('private-', json.dumps(model.complete.call_args.args))
        self.assertEqual(audit.call_args.args[:2], ('llm.sre_triage', 'success'))
        self.assertTrue(audit.call_args.kwargs['inference'])
        self.assertFalse(tracer.spans[0][1]['record_exception'])

    def test_healthy_observed_scope_completes_without_followup_reads(self):
        before = snapshots(critical=False)
        model = Mock()
        model.complete.return_value = inference(valid_triage(evidence_from(before)))
        graph, options, read, _, _ = self.run_graph(model, before=before)
        result = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(result['triage']['checks'], [])
        self.assertEqual(read.await_count, 3)
        self.assertEqual(result['followup_evidence'], {})
        self.assertFalse(graph.get_state(options).next)

    def test_invalid_model_plan_is_audited_blocked_before_any_followup_read(self):
        evidence = evidence_from(snapshots())
        valid = valid_triage(evidence)
        invalid = [{**valid, 'root_cause': 'a guessed cause'}, {**valid, 'assessment': 'healthy-observed-scope'},
                   {**valid, 'checks': []}, {**valid, 'checks': [{'tool': 'delete_pod', 'finding_ids': ['f01']}]},
                   {**valid, 'checks': [{'tool': 'k8s_read_events', 'finding_ids': ['f01']}]},
                   {**valid, 'checks': [{**valid['checks'][0], 'arguments': {'namespace': 'kube-system'}}]}]
        for proposed in invalid:
            with self.subTest(proposed=proposed):
                model = Mock()
                model.complete.return_value = inference(proposed)
                graph, options, read, audit, _ = self.run_graph(model)
                with self.assertRaises(RUN.SREError):
                    asyncio.run(graph.ainvoke({}, options))
                self.assertEqual([call.args[1] for call in read.await_args_list], ['initial'] * 3)
                self.assertEqual(audit.call_args.args[:2], ('llm.sre_triage', 'blocked'))
                self.assertEqual(audit.call_args.args[2]['completion_tokens'], 45)
                self.assertNotIn('triage', graph.get_state(options).values)
                self.assertNotIn('comparisons', graph.get_state(options).values)

    def test_failed_model_keeps_measured_usage_and_never_reobserves(self):
        model = Mock()
        model.complete.side_effect = ProviderError('private-provider-error',
            {'prompt_tokens': 123, 'completion_tokens': 45, 'duration_ms': 12})
        graph, options, read, audit, _ = self.run_graph(model)
        with self.assertRaises(RUN.SREError) as raised:
            asyncio.run(graph.ainvoke({}, options))
        self.assertNotIn('private-provider-error', str(raised.exception))
        self.assertEqual([call.args[1] for call in read.await_args_list], ['initial'] * 3)
        self.assertEqual(audit.call_args.args[:2], ('llm.sre_triage', 'failed'))
        self.assertEqual(audit.call_args.args[2], {'prompt_tokens': 123, 'completion_tokens': 45,
                                                'duration_ms': 12, 'inference_cost_estimate': None})

    def test_audit_failure_stops_even_valid_triage_before_followup(self):
        model = Mock()
        model.complete.return_value = inference(valid_triage(evidence_from(snapshots())))
        audit = Mock(side_effect=RUN.SREError('audit unavailable'))
        graph, options, read, _, _ = self.run_graph(model, audit=audit)
        with self.assertRaises(RUN.SREError):
            asyncio.run(graph.ainvoke({}, options))
        self.assertEqual([call.args[1] for call in read.await_args_list], ['initial'] * 3)
        self.assertNotIn('comparisons', graph.get_state(options).values)
        model.complete.assert_called_once()

    def test_initial_observation_failure_prevents_model_inference(self):
        model, read, audit = Mock(), AsyncMock(side_effect=RUN.SREError('read unavailable')), Mock()
        graph = RUN.build_graph(model, read, audit, FakeTracer())
        with self.assertRaises(RUN.SREError):
            asyncio.run(graph.ainvoke({}, {'configurable': {'thread_id': 'observation-failed'}}))
        model.complete.assert_not_called()
        audit.assert_not_called()

    def evaluate(self, directory, model, client):
        return EVALUATION.evaluate(local_config(), Path(directory) / 'evidence', model=model,
                                   client=client, audit_module=EVALUATION.agenthouse_module())

    def test_synthetic_sre_evaluation_all_fixtures_pass_with_audited_valid_selector(self):
        model, client = FixtureSelector(), EvaluationClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(directory, model, client)
            saved = json.loads((Path(directory) / 'evidence/result.json').read_text())
        self.assertEqual(report, saved)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['requested_inferences'], 12)
        self.assertEqual(report['attempted_inferences'], 12)
        self.assertEqual(report['passed_inferences'], 12)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertEqual(report['retries'], 0)
        self.assertEqual(len(model.calls), 12)
        self.assertEqual(model.calls, [('sre_triage', case['evidence']) for case in EVALUATION.cases() for _ in range(2)])
        first_case = [item for item in report['attempts'] if item['case_id'] == 'intentional_flux_only']
        self.assertEqual([item['selected_tools'] for item in first_case], [[], []])
        critical = [item for item in report['attempts'] if item['case_id'] == 'critical_pod']
        self.assertTrue(all('k8s_read_pods' in item['selected_tools'] for item in critical))
        self.assertEqual(len({event['event_id'] for event in client.events}), 12)
        for event in client.events:
            self.assertEqual(event['task_id'], report['task_id'])
            self.assertEqual(event['trace_id'], report['trace_id'])
            self.assertEqual(event['action_type'], 'llm.sre_eval')
            self.assertIsNone(event['inference_cost_estimate'])

    def test_synthetic_sre_evaluation_rejects_omitted_critical_findings_without_retry(self):
        model, client = FixtureSelector(omit_critical=True), EvaluationClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(directory, model, client)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['attempted_inferences'], 12)
        self.assertEqual(report['passed_inferences'], 10)
        self.assertEqual(report['audited_inferences'], 12)
        rejected = [item for item in report['attempts'] if item['result'] == 'failed']
        self.assertEqual([item['case_id'] for item in rejected], ['critical_pod', 'critical_pod'])
        self.assertTrue(all(item['failure_code'] == 'triage_contract_rejected' for item in rejected))
        self.assertEqual([event['status'] for event in client.events].count('failed'), 2)
        self.assertEqual(len(model.calls), 12)

    def test_synthetic_sre_evaluation_retains_failed_inference_usage_but_not_raw_error(self):
        model, client = FixtureSelector(fail_first=True), EvaluationClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(directory, model, client)
            saved = '\n'.join(file.read_text() for file in (Path(directory) / 'evidence').iterdir())
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['passed_inferences'], 11)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertEqual(report['attempts'][0]['failure_code'], 'model_response_failed')
        self.assertEqual(report['attempts'][0]['metrics']['completion_tokens'], 45)
        self.assertEqual(report['usage']['prompt_tokens'], 123 * 12)
        self.assertEqual(report['usage']['completion_tokens'], 45 * 12)
        self.assertIsNone(report['usage']['inference_cost_estimate'])
        self.assertNotIn('private-model-error', saved)
        self.assertNotIn('private-output', saved)
        self.assertEqual(client.events[0]['status'], 'failed')
        self.assertEqual(len(model.calls), 12)

    def test_synthetic_sre_evaluation_audit_outage_stops_further_inference(self):
        for preflight in (False, True):
            model = FixtureSelector()
            client = EvaluationClient(preflight_fails=preflight, insert_fails=True)
            with self.subTest(preflight=preflight), tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(directory, model, client)
                retained = (Path(directory) / 'evidence/result.json').read_text()
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['audited_inferences'], 0)
            self.assertEqual(report['attempted_inferences'], 0 if preflight else 1)
            self.assertEqual(len(model.calls), 0 if preflight else 1)
            self.assertNotIn('private-audit-', retained)
            if not preflight:
                self.assertEqual(report['attempts'][0]['audit'], 'failed')


if __name__ == '__main__':
    unittest.main()
