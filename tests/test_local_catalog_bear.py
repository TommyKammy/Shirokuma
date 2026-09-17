"""Catalog Curator contracts, semantic evaluation, and audited application boundaries."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
import catalog_contract as CONTRACT
import catalog_provider as PROVIDER
import evaluate_catalog as EVALUATE
import run_catalog as RUN
from providers import ProviderError


def config():
    value = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
    value['active_provider'] = 'local'
    value['providers']['local'].update(enabled=True, model='qwen3.8-flash-next',
        max_output_tokens=512, timeout_seconds=60, enable_thinking=False)
    return value


def all_missing():
    return {target: {field: True for field in CONTRACT.FIELDS} for target in CONTRACT.TARGETS}


def all_actions():
    return [{'target': target, 'field': field} for target in ('stg', 'mart') for field in ('description', 'owner', 'lineage')]


def plan(actions=None, decision='curate'):
    return {'schema_version': 1, 'decision': decision, 'actions': all_actions() if actions is None else actions,
            'evidence_ids': ['e01', 'e02', 'e03', 'e04', 'e05', 'e06']}


def response(content):
    return {'content': content, 'prompt_tokens': 123, 'completion_tokens': 45,
            'duration_ms': 12, 'inference_cost_estimate': None}


class ContractTests(unittest.TestCase):
    def test_missing_flags_project_only_fixed_target_fields_and_known_evidence_groups(self):
        evidence = CONTRACT.build_evidence(all_missing())
        self.assertEqual(evidence['targets'], [{'id': 'stg', 'missing_fields': ['description', 'owner', 'lineage']},
                                              {'id': 'mart', 'missing_fields': ['description', 'owner', 'lineage']}])
        self.assertEqual(CONTRACT.validate_plan(plan(), evidence, executable=True), plan())
        reversed_plan = plan(list(reversed(all_actions())))
        reversed_plan['evidence_ids'].reverse()
        self.assertEqual(CONTRACT.validate_plan(reversed_plan, evidence), plan())

    def test_partial_and_complete_observations_require_exact_changes_or_noop(self):
        flags = {target: {field: False for field in CONTRACT.FIELDS} for target in CONTRACT.TARGETS}
        evidence = CONTRACT.build_evidence(flags)
        self.assertEqual(CONTRACT.validate_plan(plan([], 'noop'), evidence), plan([], 'noop'))
        flags['mart']['owner'] = True
        evidence = CONTRACT.build_evidence(flags)
        self.assertEqual(CONTRACT.validate_plan(plan([{'target': 'mart', 'field': 'owner'}]), evidence),
                         plan([{'target': 'mart', 'field': 'owner'}]))
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_plan(plan(), evidence)

    def test_unverified_and_unrelated_scope_allow_only_nonexecutable_refusal(self):
        for change in ({'verified': False}, {'scope': 'out-of-scope'}):
            evidence = {**CONTRACT.build_evidence(all_missing()), **change}
            self.assertEqual(CONTRACT.validate_plan(plan([], 'unsupported'), evidence), plan([], 'unsupported'))
            for value, executable in [(plan(), False), (plan([], 'noop'), False), (plan([], 'unsupported'), True)]:
                with self.assertRaises(CONTRACT.ContractError):
                    CONTRACT.validate_plan(value, evidence, executable=executable)

    def test_plan_rejects_omissions_duplicates_invented_ids_raw_content_and_owner_choices(self):
        edits = [lambda x: x['actions'].pop(), lambda x: x['actions'].append(x['actions'][0]),
                 lambda x: x['actions'][0].update(target='fixture'),
                 lambda x: x['actions'][0].update(field='sql'),
                 lambda x: x['actions'][0].update(owner='private-user'),
                 lambda x: x.update(description='private-description'),
                 lambda x: x.update(path='/private/source'), lambda x: x.update(schema_version=True),
                 lambda x: x['evidence_ids'].pop(), lambda x: x['evidence_ids'].__setitem__(0, 'e99'),
                 lambda x: x['evidence_ids'].__setitem__(0, 'e02')]
        evidence = CONTRACT.build_evidence(all_missing())
        for edit in edits:
            value = plan()
            edit(value)
            with self.subTest(edit=edit), self.assertRaises(CONTRACT.ContractError) as error:
                CONTRACT.validate_plan(value, evidence)
            self.assertNotIn('private', str(error.exception))

    def test_evidence_rejects_raw_fields_wrong_targets_untyped_flags_or_missing_proofs(self):
        evidence = CONTRACT.build_evidence(all_missing())
        edits = [lambda x: x.update(raw_text='private'), lambda x: x.update(verified=1),
                 lambda x: x.update(schema_version=True), lambda x: x.update(scope='other'),
                 lambda x: x['targets'][0].update(id='fixture'),
                 lambda x: x['targets'][0]['missing_fields'].append('owner'),
                 lambda x: x['targets'][0]['missing_fields'].append('commands'),
                 lambda x: x['evidence_ids'].pop()]
        for edit in edits:
            value = copy.deepcopy(evidence)
            edit(value)
            with self.assertRaises(CONTRACT.ContractError):
                CONTRACT.validate_evidence(value)
        flags = all_missing()
        flags['stg']['owner'] = 'missing'
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.build_evidence(flags)

    def test_six_golden_cases_cover_missing_noop_and_both_refusal_reasons(self):
        cases = CONTRACT.model_cases()
        self.assertEqual([case['expected']['decision'] for case in cases],
                         ['curate', 'curate', 'curate', 'noop', 'unsupported', 'unsupported'])
        self.assertEqual([len(case['expected']['actions']) for case in cases], [6, 2, 2, 0, 0, 0])
        self.assertEqual(cases[2]['expected']['actions'], [{'target': 'stg', 'field': 'owner'},
                                                        {'target': 'mart', 'field': 'lineage'}])


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
            selected = plan([], 'noop') if selected['decision'] != 'noop' else plan()
        return response(selected)


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
        if matched:
            ids = {matched.group(1)}
        elif 'WHERE event_id IN (' in sql:
            ids = set(re.findall(r"'([a-f0-9-]{36})'", sql))
        else:
            raise AssertionError('unexpected audit query')
        rows = [copy.deepcopy(row) for row in self.events if row['event_id'] in ids]
        for row in rows:
            for key in ('prompt_tokens', 'completion_tokens', 'duration_ms'):
                if row[key] is not None:
                    row[key] = str(row[key])
            if self.wrong_readback:
                row['completion_tokens'] = '99'
        return json.dumps({'data': rows})


class ProviderEvaluationTests(unittest.TestCase):
    def test_provider_request_is_bounded_local_and_contains_only_projected_fields(self):
        model = PROVIDER.CatalogProvider(config()['providers']['local'])
        evidence = CONTRACT.build_evidence(all_missing())
        body = model.request_body('catalog_plan', evidence)
        self.assertIs(body['enable_thinking'], False)
        self.assertIs(body['stream'], False)
        self.assertEqual(body['max_tokens'], 512)
        self.assertLessEqual(len(json.dumps(body).encode()), 8192)
        self.assertEqual(json.loads(body['messages'][-1]['content']), {'phase': 'catalog_plan', 'evidence': evidence})
        model.opener.open = Mock()
        for phase, value in [('other', evidence), ('catalog_plan', {**evidence, 'raw_sql': 'private'})]:
            with self.assertRaises((ProviderError, CONTRACT.ContractError)):
                model.complete(phase, value)
        model.opener.open.assert_not_called()
        with self.assertRaises(ProviderError):
            PROVIDER.CatalogProvider({**config()['providers']['local'], 'kind': 'cloud'})

    def evaluate(self, model, client, directory):
        return EVALUATE.evaluate(config(), Path(directory) / 'evidence', model=model,
                                 client=client, audit_module=EVALUATE.agenthouse_module())

    def test_all_golden_cases_twice_with_safe_usage_and_correlated_audit(self):
        model, client = Selector(), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
            self.assertEqual(json.loads((Path(directory) / 'evidence/result.json').read_text()), report)
            self.assertEqual((Path(directory) / 'evidence/result.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['passed_inferences'], 12)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertEqual(report['retries'], 0)
        self.assertEqual(len(model.calls), 12)
        self.assertEqual(report['usage']['completion_tokens'], 45 * 12)
        self.assertEqual(len({event['event_id'] for event in client.events}), 12)
        for event in client.events:
            self.assertEqual(event['task_id'], report['task_id'])
            self.assertEqual(event['trace_id'], report['trace_id'])
            self.assertIsNone(event['inference_cost_estimate'])

    def test_semantically_wrong_but_bounded_plans_fail_each_golden_case(self):
        model, client = Selector(incorrect=True), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['passed_inferences'], 0)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertTrue(all(attempt['failure_code'] == 'catalog_plan_rejected' for attempt in report['attempts']))

    def test_failed_inference_keeps_usage_but_no_raw_content_or_retry(self):
        model, client = Selector(fail_first=True), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
            raw = '\n'.join(file.read_text() for file in (Path(directory) / 'evidence').iterdir())
        self.assertEqual(report['passed_inferences'], 11)
        self.assertEqual(report['audited_inferences'], 12)
        self.assertEqual(report['usage']['prompt_tokens'], 123 * 12)
        self.assertNotIn('private-model-error', raw)
        self.assertNotIn('private-raw-content', raw)
        self.assertEqual(len(model.calls), 12)

    def test_audit_failures_stop_later_model_calls(self):
        for options in ({'preflight_fails': True}, {'insert_fails': True}, {'wrong_readback': True}):
            model, client = Selector(), AuditClient(**options)
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, client, directory)
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['audited_inferences'], 0)
            self.assertEqual(len(model.calls), 0 if options.get('preflight_fails') else 1)


TOKEN = 'a' * 32
TABLE_IDS = {'stg': '11111111-1111-1111-1111-111111111111', 'mart': '22222222-2222-2222-2222-222222222222'}


def projection():
    source = 'shirokuma_local.iceberg.smoke.fixture_v1'
    names = ['shirokuma_local.iceberg.local_experiment.dataops_' + TOKEN + '_' + target for target in ('stg', 'mart')]
    return {'schema_version': 1, 'run_token': TOKEN, 'operation': 'name_lengths', 'source': source,
        'columns': [{'name': 'id', 'type': 'BIGINT'}, {'name': 'name_length', 'type': 'BIGINT'}], 'row_count': 2,
        'entities': [{'id': 'stg', 'name': names[0], 'historical': True,
                      'columns': [{'name': 'id', 'type': 'BIGINT'}, {'name': 'name', 'type': 'VARCHAR'}]},
                     {'id': 'mart', 'name': names[1], 'historical': True,
                      'columns': [{'name': 'id', 'type': 'BIGINT'}, {'name': 'name_length', 'type': 'BIGINT'}]}],
        'lineage': [{'from': source, 'to': names[0]}, {'from': names[0], 'to': names[1]}],
        'evidence': [{'id': f'e{index:02d}', 'kind': kind, 'sha256': 'b' * 64}
                     for index, kind in enumerate(('plan', 'project', 'execution', 'dbt-manifests', 'dbt-results', 'dagster'), 1)],
        'dagster_run_ids': ['33333333-3333-3333-3333-333333333333', '44444444-4444-4444-4444-444444444444'],
        'cleanup_verified': True, 'fixture_unchanged': True}


def observed(*, complete=False):
    value = projection()
    return {'projection': value, 'catalog_state': {'run_token': TOKEN, 'evidence_sha256': RUN.digest(value),
        'table_ids': dict(TABLE_IDS) if complete else {'stg': None, 'mart': None},
        'targets': {target: {field: complete for field in CONTRACT.FIELDS} for target in CONTRACT.TARGETS}}}


def receipt(*, noop=False):
    return {'result': 'passed', 'run_token': TOKEN, 'evidence_sha256': RUN.digest(projection()),
        'created_tables': 0 if noop else 2, 'reused_tables': 2 if noop else 0,
        'created_edges': 0 if noop else 2, 'verified_tables': 2, 'verified_edges': 2,
        'writes': 0 if noop else 4, 'noop': noop, 'historical_models_removed_from_trino': True,
        'table_ids': dict(TABLE_IDS), 'targets': {target: {field: True for field in CONTRACT.FIELDS}
                                               for target in CONTRACT.TARGETS}}


class FakeTracer:
    def start_as_current_span(self, name, **options):
        return contextlib.nullcontext(Mock())


class WorkflowTests(unittest.TestCase):
    def test_runner_wires_private_progress_and_verifies_all_three_audit_stores_without_live_services(self):
        audit_client = AuditClient()
        agenthouse = EVALUATE.agenthouse_module()
        loki = Mock()
        logs = []
        loki.push_record.side_effect = lambda value: logs.append(copy.deepcopy(value))
        loki.query_task.side_effect = lambda task: [row for row in logs if row['task_id'] == task]
        applied = []

        def apply(client, source, state, *, progress):
            repeated = len(applied) == 1
            progress({'run_token': TOKEN, 'result': 'in-progress', 'pending': {'operation': 'create_table', 'target': 'stg'}})
            value = receipt(noop=repeated)
            applied.append(value)
            progress({'run_token': TOKEN, 'result': 'passed', 'pending': None, 'verified_complete': True})
            return value

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(RUN, 'CatalogProvider', return_value=Selector()), \
                patch.object(RUN, 'check_runtime'), patch.object(RUN, 'MetadataClient', return_value=Mock()), \
                patch.object(RUN, 'agenthouse_module', return_value=agenthouse), \
                patch.object(agenthouse, 'Client', return_value=audit_client), patch.object(RUN, 'module', return_value=loki), \
                patch.object(RUN.data_plan_evidence, 'read_evidence', return_value=projection()) as read_evidence, \
                patch.object(RUN.curate, 'preflight', side_effect=[observed()['catalog_state'], observed(complete=True)['catalog_state']]), \
                patch.object(RUN.curate, 'apply', side_effect=apply):
            output = Path(directory) / 'catalog'
            report = asyncio.run(RUN.run(config(), TOKEN, output))
            self.assertEqual(report['result'], 'passed')
            self.assertEqual(report['correlated_events'], 5)
            self.assertEqual(report['saved_otel_spans_verified'], 5)
            self.assertEqual(report['checkpoint'], 'completed-in-memory')
            self.assertEqual(read_evidence.call_count, 3)
            for filename in ('apply-progress.json', 'repeat-progress.json', 'spans.jsonl', 'result.json'):
                self.assertEqual((output / filename).stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((output / 'apply-progress.json').read_text())['verified_complete'], True)
            self.assertEqual(json.loads((output / 'repeat.json').read_text())['writes'], 0)

    def graph(self, *, observation=None, model=None, apply=None, audit=None):
        observation = observed() if observation is None else observation
        evidence = RUN.validate_observation(observation)['evidence']
        model = model or Mock(complete=Mock(return_value=response(CONTRACT.expected_plan(evidence))))
        reader = AsyncMock(return_value=observation)
        noop = not any(target['missing_fields'] for target in evidence['targets'])
        apply = apply or AsyncMock(side_effect=[receipt(noop=noop), receipt(noop=True)])
        audit = audit or Mock()
        graph = RUN.build_graph(model, reader, apply, audit, FakeTracer())
        return graph, {'configurable': {'thread_id': 'catalog-test'}, 'recursion_limit': 12}, model, reader, apply, audit

    def test_exact_plan_applies_two_entities_two_edges_then_verified_noop(self):
        graph, options, model, read, apply, audit = self.graph()
        result = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(result['verification']['tables'], 2)
        self.assertEqual(result['verification']['lineage_edges'], 2)
        self.assertIs(result['verification']['repeat_noop'], True)
        self.assertEqual(apply.await_count, 2)
        self.assertIsNone(apply.await_args_list[0].args[2])
        self.assertEqual(apply.await_args_list[1].args[2], receipt())
        self.assertEqual([call.args[:2] for call in audit.call_args_list], [('bear.catalog.observe', 'success'),
            ('llm.catalog_plan', 'success'), ('bear.catalog.apply', 'success'), ('bear.catalog.repeat', 'success')])
        self.assertEqual(graph.get_state(options).next, ())
        self.assertEqual(graph.get_state(options).values['verification'], result['verification'])
        model_input = model.complete.call_args.args[1]
        self.assertNotIn(TOKEN, json.dumps(model_input))
        self.assertNotIn('shirokuma_local', json.dumps(model_input))
        self.assertEqual(model_input['evidence_ids'], list(CONTRACT.EVIDENCE_IDS))

    def test_already_curated_run_requires_noop_both_times(self):
        graph, options, _, _, _, _ = self.graph(observation=observed(complete=True))
        result = asyncio.run(graph.ainvoke({}, options))
        self.assertEqual(result['plan']['decision'], 'noop')
        self.assertEqual(result['applied']['writes'], 0)
        self.assertEqual(result['repeated']['writes'], 0)

    def test_wrong_plan_omission_or_arbitrary_owner_never_reaches_apply(self):
        for value in (plan([], 'noop'), plan(all_actions()[:-1]), {**plan(), 'owner': 'private-user'},
                      {**plan(), 'evidence_ids': ['e01']}):
            model = Mock(complete=Mock(return_value=response(value)))
            graph, options, _, _, apply, audit = self.graph(model=model)
            with self.subTest(plan=value), self.assertRaises(RUN.CatalogError):
                asyncio.run(graph.ainvoke({}, options))
            apply.assert_not_awaited()
            self.assertEqual(audit.call_args.args[:2], ('llm.catalog_plan', 'blocked'))
            self.assertEqual(audit.call_args.args[2]['completion_tokens'], 45)

    def test_model_failure_keeps_usage_and_audit_failure_prevents_mutation(self):
        model = Mock(complete=Mock(side_effect=ProviderError('private error', {'prompt_tokens': 21,
            'completion_tokens': 3, 'duration_ms': 9})))
        graph, options, _, _, apply, audit = self.graph(model=model)
        with self.assertRaises(RUN.CatalogError):
            asyncio.run(graph.ainvoke({}, options))
        apply.assert_not_awaited()
        self.assertEqual(audit.call_args.args[:2], ('llm.catalog_plan', 'failed'))
        self.assertEqual(audit.call_args.args[2]['prompt_tokens'], 21)
        for failed_phase in ('bear.catalog.observe', 'llm.catalog_plan', 'bear.catalog.apply'):
            def record(action, *args, **kwargs):
                if action == failed_phase:
                    raise RUN.CatalogError('audit unavailable')
            graph, options, model, _, apply, _ = self.graph(audit=Mock(side_effect=record))
            with self.subTest(phase=failed_phase), self.assertRaises(RUN.CatalogError):
                asyncio.run(graph.ainvoke({}, options))
            self.assertEqual(apply.await_count, 1 if failed_phase == 'bear.catalog.apply' else 0)
            if failed_phase == 'bear.catalog.observe':
                model.complete.assert_not_called()

    def test_apply_failure_is_audited_without_retry_or_false_completion(self):
        async def failed_apply(*args):
            await asyncio.sleep(0.01)
            raise RuntimeError('private-api-output')
        apply = AsyncMock(side_effect=failed_apply)
        graph, options, _, _, _, audit = self.graph(apply=apply)
        with self.assertRaises(RUN.CatalogError) as raised:
            asyncio.run(graph.ainvoke({}, options))
        self.assertNotIn('private-api-output', str(raised.exception))
        self.assertEqual(apply.await_count, 1)
        self.assertEqual(audit.call_args.args[:2], ('bear.catalog.apply', 'failed'))
        self.assertGreaterEqual(audit.call_args.args[2]['duration_ms'], 5)
        self.assertNotIn('verification', graph.get_state(options).values)

    def test_readback_omissions_identity_drift_or_non_noop_repeat_fail(self):
        edits = [lambda x: x.update(verified_edges=1), lambda x: x.update(writes=True),
                 lambda x: x.update(historical_models_removed_from_trino=False),
                 lambda x: x.update(run_token='c' * 32),
                 lambda x: x['targets']['mart'].update(owner=False),
                 lambda x: x['table_ids'].update(mart=TABLE_IDS['stg'])]
        for edit in edits:
            bad = receipt()
            edit(bad)
            graph, options, _, _, apply, audit = self.graph(apply=AsyncMock(return_value=bad))
            with self.subTest(edit=edit), self.assertRaises(RUN.CatalogError):
                asyncio.run(graph.ainvoke({}, options))
            self.assertEqual(apply.await_count, 1)
            self.assertEqual(audit.call_args.args[:2], ('bear.catalog.apply', 'failed'))
        for bad in (receipt(), {**receipt(noop=True), 'table_ids': {'stg': TABLE_IDS['stg'],
                                                               'mart': '55555555-5555-5555-5555-555555555555'}}):
            graph, options, _, _, _, audit = self.graph(apply=AsyncMock(side_effect=[receipt(), bad]))
            with self.assertRaises(RUN.CatalogError):
                asyncio.run(graph.ainvoke({}, options))
            self.assertEqual(audit.call_args.args[:2], ('bear.catalog.repeat', 'failed'))

    def test_source_run_scope_and_observation_binding_reject_before_inference(self):
        self.assertEqual(RUN.source_directory(TOKEN), RUN.ROOT / 'tmp/local-experiment/data-plans' / TOKEN)
        for token in ('../other', '/private/file', TOKEN.upper(), '', TOKEN + '0'):
            with self.assertRaises(RUN.CatalogError):
                RUN.source_directory(token)
        for edit in (lambda x: x['projection'].update(cleanup_verified=False),
                     lambda x: x['catalog_state'].update(evidence_sha256='c' * 64),
                     lambda x: x['catalog_state']['targets']['stg'].update(description=True),
                     lambda x: x['catalog_state']['table_ids'].update(stg=TABLE_IDS['stg'])):
            value = observed()
            edit(value)
            model, apply, audit = Mock(), AsyncMock(), Mock()
            graph = RUN.build_graph(model, AsyncMock(return_value=value), apply, audit, FakeTracer())
            with self.assertRaises(RUN.CatalogError):
                asyncio.run(graph.ainvoke({}, {'configurable': {'thread_id': 'bad-observation'}}))
            model.complete.assert_not_called()
            apply.assert_not_awaited()
            self.assertEqual(audit.call_args.args[:2], ('bear.catalog.observe', 'failed'))

    def test_reused_target_identity_cannot_change_even_when_counts_claim_noop(self):
        observation = RUN.validate_observation(observed(complete=True))
        value = receipt(noop=True)
        value['table_ids']['mart'] = '55555555-5555-5555-5555-555555555555'
        with self.assertRaises(RUN.CatalogError):
            RUN.verify_receipt(value, observation)

    def test_observation_cancellation_is_preserved_after_failed_audit_and_no_inference_or_mutation(self):
        model, apply, audit = Mock(), AsyncMock(), Mock()
        async def cancel_during_observation():
            entered = asyncio.Event()
            async def observe():
                entered.set()
                await asyncio.Event().wait()
            graph = RUN.build_graph(model, observe, apply, audit, FakeTracer())
            running = asyncio.create_task(graph.ainvoke({}, {'configurable': {'thread_id': 'cancelled'}}))
            await entered.wait()
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        asyncio.run(cancel_during_observation())
        model.complete.assert_not_called()
        apply.assert_not_awaited()
        self.assertEqual(audit.call_args.args[:2], ('bear.catalog.observe', 'failed'))

    def test_inference_cancellation_preserves_original_even_when_failed_audit_is_unavailable(self):
        async def exercise():
            entered, release = asyncio.Event(), threading.Event()
            loop = asyncio.get_running_loop()

            def complete(*args):
                loop.call_soon_threadsafe(entered.set)
                release.wait(5)
                return response(plan())

            model, apply, audit = Mock(complete=Mock(side_effect=complete)), AsyncMock(), Mock()
            def failed_audit(action, *args, **kwargs):
                if action == 'llm.catalog_plan':
                    raise RuntimeError('PRIVATE AUDIT FAILURE')
            audit.side_effect = failed_audit
            graph, options, _, _, _, _ = self.graph(model=model, apply=apply, audit=audit)
            running = asyncio.create_task(graph.ainvoke({}, options))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                running.cancel('PRIVATE CANCELLATION')
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await running
                self.assertEqual(raised.exception.args, ('PRIVATE CANCELLATION',))
            finally:
                release.set()
            apply.assert_not_awaited()
            failures = [call for call in audit.call_args_list if call.args[0] == 'llm.catalog_plan']
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].args[1], 'failed')
            self.assertIsNone(failures[0].args[2]['prompt_tokens'])
            self.assertIsNone(failures[0].args[2]['completion_tokens'])
            self.assertIsNone(failures[0].args[2]['inference_cost_estimate'])
        asyncio.run(exercise())

    def test_saved_local_spans_require_exact_audited_trace_phase_and_success(self):
        records = [{'span_id': '1' * 16, 'action_type': 'bear.catalog.apply', 'status': 'success'}]
        span = {'span_id': '1' * 16, 'trace_id': 'a' * 32, 'name': 'bear.catalog.apply', 'status': 'OK'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spans.jsonl'
            RUN.private_write(path, span)
            RUN.verify_saved_spans(path, records, 'a' * 32)
            for edit in ({'span_id': '2' * 16}, {'trace_id': 'b' * 32}, {'name': 'other'}, {'status': 'ERROR'}):
                RUN.private_write(path, {**span, **edit})
                with self.assertRaises(RUN.CatalogError):
                    RUN.verify_saved_spans(path, records, 'a' * 32)
            RUN.private_write(path, span)
            RUN.private_write(path, span, append=True)
            with self.assertRaises(RUN.CatalogError):
                RUN.verify_saved_spans(path, records, 'a' * 32)


class RunInterruptionTests(unittest.TestCase):
    @contextlib.contextmanager
    def environment(self, directory):
        from opentelemetry.sdk.trace import TracerProvider
        provider = TracerProvider()
        agenthouse, audit_client, loki, model = EVALUATE.agenthouse_module(), AuditClient(), Mock(), Mock()
        model.complete.return_value = response(plan())
        logs = []
        loki.push_record.side_effect = lambda value: logs.append(copy.deepcopy(value))
        loki.query_task.side_effect = lambda task: [row for row in logs if row['task_id'] == task]
        with contextlib.ExitStack() as stack:
            patches = [patch.object(RUN, 'CatalogProvider', return_value=model),
                patch.object(RUN, 'MetadataClient', return_value=Mock()),
                patch.object(RUN, 'agenthouse_module', return_value=agenthouse),
                patch.object(agenthouse, 'Client', return_value=audit_client),
                patch.object(RUN, 'module', return_value=loki),
                patch.object(RUN, 'disable_external_tracing'),
                patch.object(RUN.data_plan_evidence, 'read_evidence', return_value=projection()),
                patch.object(RUN.curate, 'preflight', return_value=observed()['catalog_state']),
                patch('opentelemetry.sdk.trace.TracerProvider', return_value=provider)]
            for item in patches:
                stack.enter_context(item)
            shutdown = stack.enter_context(patch.object(provider, 'shutdown', wraps=provider.shutdown))
            yield Path(directory) / 'catalog', audit_client, loki, model, shutdown

    def check_interrupted(self, output, audit_client, *, application_started=False):
        report = json.loads((output / 'result.json').read_text())
        spans = [json.loads(line) for line in (output / 'spans.jsonl').read_text().splitlines()]
        self.assertEqual(report['result'], 'interrupted')
        self.assertEqual(report['application_started'], application_started)
        self.assertNotIn('checkpoint', report)
        self.assertNotIn('verification', report)
        self.assertNotIn('rollback_performed', report)
        roots = [row for row in audit_client.events if row['action_type'] == 'bear.catalog.assess']
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]['status'], 'failed')
        self.assertIsNone(roots[0]['prompt_tokens'])
        self.assertIsNone(roots[0]['completion_tokens'])
        self.assertIsNone(roots[0]['inference_cost_estimate'])
        root_spans = [row for row in spans if row['name'] == 'bear.catalog.assess']
        self.assertEqual(len(root_spans), 1)
        self.assertEqual(root_spans[0]['status'], 'ERROR')
        self.assertTrue(all(row['audit_delivery'] == 'delivered' for row in report['audit_records']))
        self.assertNotIn('PRIVATE', json.dumps([report, spans, audit_client.events]))
        self.assertEqual((output / 'result.json').stat().st_mode & 0o777, 0o600)
        return report, spans

    def test_outer_cancellation_and_keyboard_interrupt_save_root_failure_and_shutdown_before_reraise(self):
        for interruption in (asyncio.CancelledError('PRIVATE CANCEL'), KeyboardInterrupt('PRIVATE INTERRUPT')):
            with self.subTest(kind=type(interruption).__name__), tempfile.TemporaryDirectory() as directory, \
                    self.environment(directory) as (output, audit_client, loki, model, shutdown), \
                    patch.object(RUN, 'check_runtime', side_effect=interruption), \
                    patch.object(RUN.curate, 'apply') as apply:
                with self.assertRaises(type(interruption)) as raised:
                    asyncio.run(RUN.run(config(), TOKEN, output))
                self.assertIs(raised.exception, interruption)
                shutdown.assert_called_once_with()
                model.complete.assert_not_called()
                apply.assert_not_called()
                self.check_interrupted(output, audit_client)
                loki.push_record.assert_called_once()

    def test_actual_inference_task_cancellation_closes_error_spans_keeps_unknown_usage_and_does_not_apply(self):
        with tempfile.TemporaryDirectory() as directory, \
                self.environment(directory) as (output, audit_client, loki, model, shutdown), \
                patch.object(RUN, 'check_runtime'), patch.object(RUN.curate, 'apply') as apply:
            async def exercise():
                entered, release = asyncio.Event(), threading.Event()
                loop = asyncio.get_running_loop()

                def complete(*args):
                    loop.call_soon_threadsafe(entered.set)
                    release.wait(5)
                    return response(plan())

                model.complete.side_effect = complete
                running = asyncio.create_task(RUN.run(config(), TOKEN, output))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    running.cancel('PRIVATE CANCEL')
                    with self.assertRaises(asyncio.CancelledError):
                        await running
                finally:
                    release.set()
            asyncio.run(exercise())
            shutdown.assert_called_once_with()
            apply.assert_not_called()
            report, spans = self.check_interrupted(output, audit_client)
            self.assertEqual(len(audit_client.events), 3)
            inference = next(row for row in audit_client.events if row['action_type'] == 'llm.catalog_plan')
            self.assertEqual(inference['status'], 'failed')
            self.assertIsNone(inference['prompt_tokens'])
            self.assertIsNone(inference['completion_tokens'])
            self.assertIsNone(inference['inference_cost_estimate'])
            self.assertEqual(next(row['status'] for row in spans if row['name'] == 'llm.catalog_plan'), 'ERROR')
            self.assertEqual(loki.push_record.call_count, 3)

    def test_final_receipt_write_failure_preserves_original_interruption_only(self):
        for interruption in (asyncio.CancelledError('PRIVATE CANCEL'), None):
            with self.subTest(interrupted=interruption is not None), tempfile.TemporaryDirectory() as directory, \
                    self.environment(directory) as (output, audit_client, loki, model, shutdown), \
                    patch.object(RUN, 'check_runtime', side_effect=interruption or RuntimeError('PRIVATE FAILURE')):
                original_write = RUN.private_write
                write_error = OSError('PRIVATE WRITE FAILURE')
                final_attempts = []

                def fail_final_write(path, value, **kwargs):
                    if shutdown.call_count and path.name == 'result.json':
                        final_attempts.append(value['result'])
                        raise write_error
                    return original_write(path, value, **kwargs)

                with patch.object(RUN, 'private_write', side_effect=fail_final_write):
                    with self.assertRaises(asyncio.CancelledError if interruption is not None else OSError) as raised:
                        asyncio.run(RUN.run(config(), TOKEN, output))
                self.assertIs(raised.exception, interruption if interruption is not None else write_error)
                shutdown.assert_called_once_with()
                self.assertEqual(final_attempts, ['interrupted' if interruption is not None else 'failed'])
                self.assertEqual([(row['action_type'], row['status']) for row in audit_client.events],
                                 [('bear.catalog.assess', 'failed')])
                spans = [json.loads(line) for line in (output / 'spans.jsonl').read_text().splitlines()]
                self.assertEqual(spans[0]['status'], 'ERROR')

    def test_partial_metadata_progress_survives_interruption_without_repeat_or_rollback_claim(self):
        interruption = RUN.SessionInterrupted(15)
        journal = {'run_token': TOKEN, 'result': 'in-progress', 'confirmed_tables': {},
                   'pending': {'operation': 'create_table', 'target': 'stg', 'outcome': 'unknown'},
                   'verified_complete': False}

        def interrupted_apply(client, source, state, *, progress):
            progress(copy.deepcopy(journal))
            raise interruption

        with tempfile.TemporaryDirectory() as directory, \
                self.environment(directory) as (output, audit_client, loki, model, shutdown), \
                patch.object(RUN, 'check_runtime'), \
                patch.object(RUN.curate, 'apply', side_effect=interrupted_apply) as apply:
            with self.assertRaises(RUN.SessionInterrupted) as raised:
                asyncio.run(RUN.run(config(), TOKEN, output))
            self.assertIs(raised.exception, interruption)
            shutdown.assert_called_once_with()
            self.assertEqual(apply.call_count, 1)
            self.check_interrupted(output, audit_client, application_started=True)
            self.assertEqual(json.loads((output / 'apply-progress.json').read_text()), journal)
            self.assertFalse((output / 'repeat.json').exists())
            self.assertFalse((output / 'apply.json').exists())
            actions = [(row['action_type'], row['status']) for row in audit_client.events]
            self.assertIn(('bear.catalog.apply', 'failed'), actions)
            self.assertNotIn(('bear.catalog.repeat', 'success'), actions)


if __name__ == '__main__':
    unittest.main()
