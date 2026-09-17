"""Data Bear plan semantics, mutation boundary, and audit failures; no live services."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
import data_contract as CONTRACT
import data_provider as PROVIDER
import evaluate_data as EVALUATE
import run_data as RUN
from providers import ProviderError


def config():
    value = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
    value['active_provider'] = 'local'
    value['providers']['local'].update(enabled=True, model='qwen3.8-flash-next',
        max_output_tokens=512, timeout_seconds=60, enable_thinking=False)
    return value


def plan(operation='name_lengths'):
    return {'schema_version': 1, 'source': 'fixture_v1', 'operation': operation}


def response(value=None):
    return {'content': plan() if value is None else value, 'prompt_tokens': 123, 'completion_tokens': 45,
            'duration_ms': 12, 'inference_cost_estimate': None}


class Selector:
    def __init__(self, *, wrong=False, invalid=False, fail_first=False):
        self.calls = []
        self.wrong, self.invalid, self.fail_first = wrong, invalid, fail_first

    def complete(self, phase, evidence):
        self.calls.append((phase, copy.deepcopy(evidence)))
        if self.fail_first and len(self.calls) == 1:
            raise ProviderError('private-error', {'prompt_tokens': 123, 'completion_tokens': 45,
                'duration_ms': 12, 'content': 'private-output'})
        operation = next(case['expected_operation'] for case in CONTRACT.model_cases() if case['request'] == evidence['request'])
        if self.wrong:
            operation = 'row_count' if operation != 'row_count' else 'name_lengths'
        value = plan(operation)
        if self.invalid:
            value['sql'] = 'private-model-output'
        return response(value)


class AuditClient:
    def __init__(self, *, preflight_fails=False, insert_fails=False, wrong_readback=False):
        self.events = []
        self.preflight_fails, self.insert_fails, self.wrong_readback = preflight_fails, insert_fails, wrong_readback

    def query(self, sql):
        if sql == 'SELECT 1':
            if self.preflight_fails:
                raise RuntimeError('private-preflight')
            return '1\n'
        if sql.startswith('INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n'):
            if self.insert_fails:
                raise RuntimeError('private-insert')
            self.events.append(json.loads(sql.split('\n', 1)[1]))
            return ''
        matched = re.search(r"WHERE event_id = '([a-f0-9-]{36})' FORMAT JSON$", sql)
        if not matched:
            raise AssertionError('unexpected audit query')
        rows = [copy.deepcopy(row) for row in self.events if row['event_id'] == matched.group(1)]
        for row in rows:
            for key in ('prompt_tokens', 'completion_tokens', 'duration_ms'):
                if row[key] is not None:
                    row[key] = str(row[key])
            if self.wrong_readback:
                row['completion_tokens'] = '99'
        return json.dumps({'data': rows})


class ProviderEvaluationTests(unittest.TestCase):
    def test_provider_preserves_exact_fixed_schema_and_thinking_with_bounded_request(self):
        evidence = {'request': CONTRACT.model_cases()[0]['request'], 'schema': CONTRACT.schema_evidence()}
        profile = config()['providers']['local']
        body = PROVIDER.DataProvider(profile).request_body('data_plan', evidence)
        self.assertEqual(json.loads(body['messages'][-1]['content']), {'phase': 'data_plan', 'evidence': evidence})
        self.assertEqual(body['max_tokens'], 512)
        self.assertIs(body['enable_thinking'], False)
        self.assertIs(body['stream'], False)
        self.assertLessEqual(len(json.dumps(body).encode()), 8192)
        profile.pop('enable_thinking')
        self.assertNotIn('enable_thinking', PROVIDER.DataProvider(profile).request_body('data_plan', evidence))

    def test_unknown_phase_schema_extra_fields_or_unbounded_request_fail_before_http(self):
        good = {'request': CONTRACT.model_cases()[0]['request'], 'schema': CONTRACT.schema_evidence()}
        values = [None, {**good, 'sql': 'private'}, {**good, 'schema': {'source': 'other'}},
                  {**good, 'request': 'a' * 513}, {**good, 'request': ' '}, {**good, 'request': 'bad\x00text'}]
        model = PROVIDER.DataProvider(config()['providers']['local'])
        model.opener.open = Mock()
        for phase, value in [('other', good)] + [('data_plan', item) for item in values]:
            with self.subTest(phase=phase, value=value), self.assertRaises(ProviderError):
                model.complete(phase, value)
        model.opener.open.assert_not_called()
        with self.assertRaises(ProviderError):
            PROVIDER.DataProvider({**config()['providers']['local'], 'kind': 'cloud'})

    def evaluate(self, model, client, directory):
        return EVALUATE.evaluate(config(), Path(directory) / 'evidence', model=model,
                                 client=client, audit_module=EVALUATE.agenthouse_module())

    def test_golden_semantics_and_refusals_each_run_twice_with_exact_audits(self):
        model, client = Selector(), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
            self.assertEqual(json.loads((Path(directory) / 'evidence/result.json').read_text()), report)
            self.assertEqual((Path(directory) / 'evidence/result.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((Path(directory) / 'evidence').stat().st_mode & 0o777, 0o700)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['requested_inferences'], 12)
        self.assertEqual(report['passed_inferences'], 12)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertEqual(len(model.calls), 12)
        self.assertEqual(report['retries'], 0)
        self.assertEqual([item['plan']['operation'] for item in report['attempts']],
                         [case['expected_operation'] for case in CONTRACT.model_cases() for _ in range(2)])
        self.assertEqual(sum(item['plan']['operation'] == 'unsupported' for item in report['attempts']), 4)
        self.assertEqual(len({event['event_id'] for event in client.events}), 12)
        for event in client.events:
            self.assertEqual(event['task_id'], report['task_id'])
            self.assertEqual(event['trace_id'], report['trace_id'])
            self.assertIsNone(event['inference_cost_estimate'])

    def test_valid_but_wrong_operation_fails_semantic_grading_and_invalid_raw_plan_is_not_saved(self):
        for model in (Selector(wrong=True), Selector(invalid=True)):
            client = AuditClient()
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, client, directory)
                text = '\n'.join(file.read_text() for file in (Path(directory) / 'evidence').iterdir())
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['passed_inferences'], 0)
            self.assertEqual(report['audited_inferences'], 12)
            self.assertTrue(all(item['failure_code'] == 'plan_contract_or_operation_rejected' for item in report['attempts']))
            self.assertNotIn('private-model-output', text)

    def test_failed_inference_usage_is_retained_without_raw_error_and_without_retry(self):
        model, client = Selector(fail_first=True), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
            text = '\n'.join(file.read_text() for file in (Path(directory) / 'evidence').iterdir())
        self.assertEqual(report['passed_inferences'], 11)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertEqual(report['attempts'][0]['metrics']['completion_tokens'], 45)
        self.assertEqual(report['usage']['prompt_tokens'], 123 * 12)
        self.assertNotIn('private-error', text)
        self.assertNotIn('private-output', text)
        self.assertEqual(len(model.calls), 12)

    def test_audit_delivery_readback_and_preflight_failures_stop_more_model_calls(self):
        for options in ({'preflight_fails': True}, {'insert_fails': True}, {'wrong_readback': True}):
            model, client = Selector(), AuditClient(**options)
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, client, directory)
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['audited_inferences'], 0)
            self.assertEqual(len(model.calls), 0 if options.get('preflight_fails') else 1)


def observed():
    return {'schema': CONTRACT.schema_evidence(), 'fixture_verified': True,
            'inventory': {'models': ['stg_fixture', 'mart_fixture'], 'verified_run_available': False}}


def receipt():
    token = 'a' * 32
    return {'result': 'passed', 'plan': plan(), 'run_token': token, 'materializations': 2,
            'fixture_unchanged': True, 'deterministic': True,
            'evidence': str(RUN.ROOT / 'tmp/local-experiment/data-plans' / token),
            'attempts': [{'label': label, 'success': True, 'materializations': 1,
                          'readback': {'columns': ['id', 'name_length'], 'rows': [[1, 9], [2, 7]]}}
                         for label in ('first', 'repeat')],
            'query_drain': {'verified': True},
            'cleanup': {'removed': [f'iceberg.local_experiment.dataops_{token}_{name}' for name in ('mart', 'stg')],
                        'remaining': [], 'errors': []}}


class FakeTracer:
    def start_as_current_span(self, name, **options):
        return contextlib.nullcontext(Mock())


class WorkflowTests(unittest.TestCase):
    def graph(self, *, model=None, observation=None, execution=None, audit=None, case_id=RUN.DEFAULT_CASE):
        model = model or Mock(complete=Mock(return_value=response()))
        read = AsyncMock(return_value=observed() if observation is None else observation)
        execute = AsyncMock(return_value=receipt() if execution is None else execution)
        audit = audit or Mock()
        graph = RUN.build_graph(model, read, execute, audit, FakeTracer(), case_id=case_id)
        options = {'configurable': {'thread_id': 'data-test'}, 'recursion_limit': 10}
        return graph, options, model, read, execute, audit

    def test_successful_fixed_plan_verifies_both_materializations_and_completed_checkpoint(self):
        graph, options, model, read, execute, audit = self.graph()
        result = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(result['verification']['rows'], [[1, 9], [2, 7]])
        self.assertEqual(result['verification']['materializations'], 2)
        execute.assert_awaited_once_with(plan())
        model.complete.assert_called_once_with('data_plan',
            {'request': RUN.smoke_case()['request'], 'schema': CONTRACT.schema_evidence()})
        self.assertEqual([call.args[:2] for call in audit.call_args_list],
                         [('llm.data_plan', 'success'), ('bear.data.materialize', 'success')])
        self.assertEqual(graph.get_state(options).next, ())
        self.assertEqual(graph.get_state(options).values['verification'], result['verification'])

    def test_all_three_fixed_cases_bind_their_own_operation_and_independent_oracle(self):
        expected = {'uppercase_names_ja': ('uppercase_names', ['id', 'name_upper'], [[1, 'SHIROKUMA'], [2, 'POLARIS']]),
                    'name_lengths_ja': ('name_lengths', ['id', 'name_length'], [[1, 9], [2, 7]]),
                    'row_count_ja': ('row_count', ['row_count'], [[2]])}
        for case_id, (operation, names, rows) in expected.items():
            value = receipt()
            value['plan'] = plan(operation)
            for attempt in value['attempts']:
                attempt['readback'] = {'columns': names, 'rows': rows}
            model = Mock(complete=Mock(return_value=response(plan(operation))))
            graph, options, _, _, execute, _ = self.graph(model=model, execution=value, case_id=case_id)
            result = asyncio.run(graph.ainvoke({}, options))
            self.assertEqual(result['verification']['rows'], rows)
            execute.assert_awaited_once_with(plan(operation))
            self.assertEqual(model.complete.call_args.args[1]['request'], RUN.smoke_case(case_id)['request'])
        for case_id in ('delete_rows_unsupported_ja', 'name_lengths_en', 'arbitrary'):
            with self.assertRaises(RUN.DataError):
                self.graph(case_id=case_id)

    def test_wrong_supported_operation_refusal_or_raw_sql_never_reaches_mutation(self):
        for value in (plan('row_count'), plan('unsupported'), {**plan(), 'sql': 'private sql'},
                      {**plan(), 'source': 'retained-other'}, {**plan(), 'schema_version': True}):
            model = Mock(complete=Mock(return_value=response(value)))
            graph, options, _, _, execute, audit = self.graph(model=model)
            with self.subTest(plan=value), self.assertRaises(RUN.DataError):
                asyncio.run(graph.ainvoke({}, options))
            execute.assert_not_awaited()
            self.assertEqual(audit.call_args.args[:2], ('llm.data_plan', 'blocked'))
            self.assertEqual(audit.call_args.args[2]['completion_tokens'], 45)

    def test_failed_model_and_failed_audit_stop_before_mutation_and_keep_usage(self):
        model = Mock(complete=Mock(side_effect=ProviderError('private error',
            {'prompt_tokens': 99, 'completion_tokens': 3, 'duration_ms': 7})))
        graph, options, _, _, execute, audit = self.graph(model=model)
        with self.assertRaises(RUN.DataError):
            asyncio.run(graph.ainvoke({}, options))
        execute.assert_not_awaited()
        self.assertEqual(audit.call_args.args[:2], ('llm.data_plan', 'failed'))
        self.assertEqual(audit.call_args.args[2]['prompt_tokens'], 99)
        graph, options, _, _, execute, _ = self.graph(audit=Mock(side_effect=RUN.DataError('audit failed')))
        with self.assertRaises(RUN.DataError):
            asyncio.run(graph.ainvoke({}, options))
        execute.assert_not_awaited()

    def test_execution_exception_is_audited_failed_with_measured_duration(self):
        async def failed_execution(value):
            await asyncio.sleep(0.01)
            raise RuntimeError('private-child-error')

        audit = Mock()
        graph = RUN.build_graph(Mock(complete=Mock(return_value=response())), AsyncMock(return_value=observed()),
                                failed_execution, audit, FakeTracer())
        with self.assertRaises(RUN.DataError) as raised:
            asyncio.run(graph.ainvoke({}, {'configurable': {'thread_id': 'execution-failed'}}))
        self.assertNotIn('private-child-error', str(raised.exception))
        self.assertEqual(audit.call_args.args[:2], ('bear.data.materialize', 'failed'))
        self.assertGreaterEqual(audit.call_args.args[2]['duration_ms'], 5)
        self.assertIsNone(audit.call_args.args[2]['prompt_tokens'])

    def test_unverified_fixture_or_wrong_schema_prevents_inference_and_execution(self):
        for update in ({'fixture_verified': False}, {'fixture_verified': 1}, {'schema': {'source': 'other'}},
                       {'inventory': {'models': ['unsafe model'], 'verified_run_available': False}}):
            graph, options, model, _, execute, audit = self.graph(observation={**observed(), **update})
            with self.subTest(update=update), self.assertRaises((RUN.DataError, ProviderError)):
                asyncio.run(graph.ainvoke({}, options))
            model.complete.assert_not_called()
            execute.assert_not_awaited()
            audit.assert_not_called()

    def test_receipt_requires_oracle_results_fixture_retention_drain_and_owned_cleanup(self):
        edits = [lambda x: x.update(plan=plan('row_count')), lambda x: x.update(fixture_unchanged=False),
                 lambda x: x.update(deterministic=False), lambda x: x.update(materializations=True),
                 lambda x: x['attempts'][1]['readback'].update(rows=[[1, 7], [2, 9]]),
                 lambda x: x['attempts'][0]['readback'].update(rows=[[True, 9], [2, 7]]),
                 lambda x: x['attempts'][0]['readback'].update(columns=['id', 'wrong']),
                 lambda x: x['query_drain'].update(verified=False),
                 lambda x: x['cleanup'].update(remaining=['leftover']),
                 lambda x: x['cleanup'].update(errors=['cleanup failed']),
                 lambda x: x['cleanup']['removed'].__setitem__(0, 'iceberg.smoke.fixture_v1')]
        for edit in edits:
            bad = receipt()
            edit(bad)
            graph, options, _, _, _, audit = self.graph(execution=bad)
            with self.subTest(edit=edit), self.assertRaises(RUN.DataError):
                asyncio.run(graph.ainvoke({}, options))
            self.assertEqual(audit.call_args.args[:2], ('bear.data.materialize', 'failed'))
            self.assertNotIn('verification', graph.get_state(options).values)

    def test_fixture_queries_are_fixed_and_schema_drift_is_rejected(self):
        schema = {'columns': ['column_name', 'data_type'], 'rows': [['id', 'bigint'], ['name', 'varchar']]}
        source = {'columns': ['id', 'name'], 'rows': copy.deepcopy(RUN.FIXTURE_ROWS)}
        client = Mock()
        client.query.side_effect = [schema, source]
        self.assertEqual(RUN.verify_fixture(client), CONTRACT.schema_evidence())
        self.assertEqual([call.args[0] for call in client.query.call_args_list], [RUN.SCHEMA_SQL, RUN.FIXTURE_SQL])
        for bad_schema, bad_source in [({**schema, 'rows': [['id', 'varchar'], ['name', 'varchar']]}, source),
                                       (schema, {**source, 'rows': [[1, 'changed'], [2, 'polaris']]}),
                                       (schema, {**source, 'rows': [[True, 'shirokuma'], [2, 'polaris']]})]:
            client.query.side_effect = [bad_schema, bad_source]
            with self.assertRaises((RUN.DataError, CONTRACT.ContractError)):
                RUN.verify_fixture(client)

    def test_adapter_uses_only_fixed_arguments_and_private_plan_and_receipt(self):
        process = Mock(returncode=0)
        process.wait.return_value = 0
        process.poll.return_value = 0

        def launch(command, **options):
            self.assertEqual(command[:2], ['rtk', 'proxy'])
            self.assertEqual(command[2:4], [str(HERE.parent / 'dataops/.venv/bin/python'),
                                            str(HERE.parent / 'dataops/plan_run.py')])
            self.assertEqual(command[-4:], ['--url', 'http://127.0.0.1:18080', '--timeout', '120'])
            self.assertEqual(json.loads(Path(command[5]).read_text()), plan())
            self.assertTrue(options['start_new_session'])
            self.assertNotIn('OPENAI_API_KEY', options['env'])
            self.assertNotIn('HTTPS_PROXY', options['env'])
            saved = Path(receipt()['evidence'])
            saved.mkdir(parents=True, mode=0o700)
            RUN.private_write(saved / 'summary.json', receipt())
            options['stdout'].write(json.dumps(receipt()))
            return process

        with tempfile.TemporaryDirectory() as directory, patch.object(RUN.subprocess, 'Popen', side_effect=launch), \
                patch.object(RUN.SESSION, 'signal_group') as signal_group, patch.object(RUN, 'ROOT', Path(directory).resolve()), \
                patch.dict(os.environ, {'OPENAI_API_KEY': 'private-secret', 'HTTPS_PROXY': 'private-proxy'}):
            result = asyncio.run(RUN.execute_adapter(plan(), 'http://127.0.0.1:18080', Path(directory)))
            self.assertEqual(result, receipt())
            self.assertEqual((Path(directory) / 'plan.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((Path(directory) / 'adapter-stdout.json').stat().st_mode & 0o777, 0o600)
            signal_group.assert_called_once_with(process, signal.SIGKILL)

    def test_saved_adapter_receipt_must_match_run_identity_permissions_and_stdout(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(RUN, 'ROOT', Path(directory).resolve()):
            value = receipt()
            saved = Path(value['evidence'])
            saved.mkdir(parents=True, mode=0o700)
            RUN.private_write(saved / 'summary.json', value)
            RUN.verify_adapter_receipt(value)
            with self.assertRaises(RUN.DataError):
                RUN.verify_adapter_receipt({**value, 'evidence': str(Path(directory) / 'other')})
            with self.assertRaises(RUN.DataError):
                RUN.verify_adapter_receipt({**value, 'materializations': 99})
            (saved / 'summary.json').chmod(0o644)
            with self.assertRaises(RUN.DataError):
                RUN.verify_adapter_receipt(value)

    def test_adapter_cancellation_waits_for_sigint_cleanup_and_never_claims_success(self):
        process = Mock(returncode=None)
        process.wait.side_effect = asyncio.CancelledError()
        process.poll.side_effect = lambda: process.returncode
        cleanup_order = []

        def stop(child, first, grace):
            self.assertIs(child, process)
            self.assertEqual((first, grace), (signal.SIGINT, 180))
            cleanup_order.append('child-cleanup-completed')
            process.returncode = -signal.SIGINT

        with tempfile.TemporaryDirectory() as directory, patch.object(RUN.subprocess, 'Popen', return_value=process), \
                patch.object(RUN.SESSION, 'stop_group', side_effect=stop) as stop_group, \
                patch.object(RUN.SESSION, 'signal_group'):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(RUN.execute_adapter(plan(), 'http://127.0.0.1:18080', Path(directory)))
            stop_group.assert_called_once()
            self.assertEqual(cleanup_order, ['child-cleanup-completed'])
            self.assertEqual(json.loads((Path(directory) / 'plan.json').read_text()), plan())
            self.assertFalse((Path(directory) / 'result.json').exists())

    def test_adapter_rejects_bad_plan_or_endpoint_without_starting_child(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(RUN.subprocess, 'Popen') as child:
            for value, url in [(plan('unsupported'), 'http://127.0.0.1:18080'),
                               (plan('row_count'), 'http://127.0.0.1:18080'),
                               (plan(), 'http://external.invalid:8080')]:
                with self.assertRaises(Exception):
                    asyncio.run(RUN.execute_adapter(value, url, Path(directory)))
            child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
