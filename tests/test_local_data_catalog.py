"""Offline durable-handoff tests with real saved-artifact validators."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment/bears'))
sys.path.insert(0, str(ROOT / 'tests'))
import run_data_catalog as H
import test_local_catalog_evidence as F
from test_local_catalog_curate import FakeClient
from catalog_contract import expected_plan

D, C, E = H.modules()


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n')
    path.chmod(0o600)


def audit(result, actions, status='success'):
    result['task_id'] = 'workflow-' + uuid.uuid4().hex
    result['trace_id'] = uuid.uuid4().hex
    result['audit_records'] = [{'event_id': str(uuid.uuid4()), 'span_id': uuid.uuid4().hex[:16],
        'action_type': action, 'audit_delivery': 'delivered', 'status': status,
        'task_id': result['task_id'], 'trace_id': result['trace_id']} for action in actions]
    result['correlated_events'] = len(actions)


class FakeWorkflows:
    def __init__(self, root):
        self.root, self.data_calls, self.catalog_calls = root, 0, []
        self.data_outcome, self.catalog_outcome = 'passed', 'passed'
        self.catalog = FakeClient()

    async def data(self, config, output, *, case_id=None, request=None):
        self.data_calls += 1
        spec = D.request_spec(case_id=case_id, request=request)
        result = {'result': self.data_outcome, 'evidence_path': str(output), **D.request_metadata(spec),
                  'checkpoint': 'completed-in-memory', 'cloud_calls': 0, 'retained_data_mutation_authorized': False}
        if self.data_outcome == 'failed':
            result.pop('checkpoint')
        elif self.data_outcome == 'unsupported':
            plan = {'schema_version': 1, 'source': 'fixture_v1', 'operation': 'unsupported'}
            result.update(plan=plan, adapter_evidence=None, verification={'dataops_started': False,
                'materializations': 0, 'plan': plan, 'reason': 'unsupported-request', 'result': 'not-executed'})
            audit(result, ['llm.data_plan', 'bear.data.assess'], 'blocked')
        else:
            operation = spec['expected_operation'] or 'name_lengths'
            plan_token = uuid.uuid4().hex
            base = H.output_path('data-plans', plan_token).parent
            base.mkdir(mode=0o700, parents=True, exist_ok=True)
            with patch.object(F, 'TOKEN', plan_token):
                source = F.fixture(base, operation)
            adapter = json.loads((source / 'summary.json').read_text())
            adapter.update(evidence=str(source))
            save(source / 'summary.json', adapter)
            for item in [source, *source.rglob('*')]:
                item.chmod(0o700 if item.is_dir() else 0o600)
            save(output / 'adapter-stdout.json', adapter)
            verification = D.verify_materialization(adapter, adapter['plan'], case_id=spec['case_id'])
            result.update(plan=adapter['plan'], verification=verification, adapter_evidence=str(source))
            audit(result, ['llm.data_plan', 'bear.data.materialize', 'bear.data.assess'])
        if self.data_outcome != 'failed':
            result['tool_calls'] = [{'event_id': str(uuid.uuid4()), 'span_id': uuid.uuid4().hex[:16],
                                    'tool': 'dbt_list_models', 'action_type': 'mcp.dbt_list_models'}]
            result['correlated_events'] += 1
        save(output / 'result.json', result)
        return result

    async def curate(self, config, source_token, output):
        self.catalog_calls.append((source_token, output.name))
        projection = E.read_evidence(H.output_path('data-plans', source_token))
        state = C.curate.preflight(self.catalog, projection)
        observation = C.validate_observation({'projection': projection, 'catalog_state': state})
        save(output / 'source-evidence.json', projection)
        save(output / 'catalog-observation.json', {key: state[key] for key in ('run_token', 'evidence_sha256', 'targets', 'table_ids')})
        plan = expected_plan(observation['evidence'])
        save(output / 'plan.json', plan)
        applied = C.curate.apply(self.catalog, projection, state,
                                progress=lambda value: save(output / 'apply-progress.json', value))
        save(output / 'apply.json', applied)
        result = {'result': self.catalog_outcome, 'evidence_path': str(output), 'source_run_token': source_token,
                  'checkpoint': 'completed-in-memory', 'cloud_calls': 0}
        if self.catalog_outcome == 'failed':
            result.pop('checkpoint')
            save(output / 'result.json', result)
            return result
        repeated = C.curate.apply(self.catalog, projection, progress=lambda value: save(output / 'repeat-progress.json', value))
        save(output / 'repeat.json', repeated)
        result.update(plan=plan, applied=applied, repeated=repeated,
            verification={'result': 'passed', 'run_token': source_token, 'tables': 2, 'lineage_edges': 2,
                          'repeat_noop': True, 'table_ids': repeated['table_ids'], 'historical_models_removed_from_trino': True})
        audit(result, ['bear.catalog.observe', 'llm.catalog_plan', 'bear.catalog.apply', 'bear.catalog.repeat', 'bear.catalog.assess'])
        spans = [{'name': item['action_type'], 'span_id': item['span_id'], 'trace_id': result['trace_id'], 'status': 'OK'}
                 for item in result['audit_records']]
        path = output / 'spans.jsonl'
        path.write_text(''.join(json.dumps(span) + '\n' for span in spans))
        path.chmod(0o600)
        result['saved_otel_spans_verified'] = 5
        save(output / 'result.json', result)
        return result


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.local = self.root / 'tmp/local-experiment'
        self.local.mkdir(mode=0o700, parents=True)
        self.token = uuid.uuid4().hex
        self.config = D.load_config(ROOT / 'tools/local_experiment/llm/providers.qwen-local.json')
        for owner, name, value in [(H, 'ROOT', self.root), (H, 'STATE', self.local / 'data-catalog'),
                                   (D, 'ROOT', self.root), (E, 'EVIDENCE_ROOT', self.local / 'data-plans')]:
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.work = FakeWorkflows(self.root)

    def journal(self):
        return H.read_json(H.STATE / self.token / 'handoff.json')[0]

    async def run_flow(self, **kwargs):
        return await H.run_handoff(self.config, self.token, data_runner=self.work.data,
                                   catalog_runner=self.work.curate, **kwargs)

    def all_hashes(self, output):
        return {str(path.relative_to(output)): H.hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.rglob('*') if path.is_file()}

    async def test_success_replay_and_same_input_run_invoke_data_once(self):
        first = await self.run_flow()
        self.assertEqual(first['result'], 'passed', first)
        before = self.all_hashes(H.STATE / self.token)
        resumed = await self.run_flow(resume=True)
        repeated = await self.run_flow()
        self.assertTrue(resumed['replayed'] and repeated['replayed'])
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))
        self.assertEqual(self.work.catalog_calls[0][0], first['data_plan_token'])
        self.assertEqual(self.all_hashes(H.STATE / self.token), before)
        self.assertEqual(self.journal()['data']['outcome'], 'passed')

    async def test_natural_request_hash_only_and_unsupported_catalog_zero(self):
        self.work.data_outcome = 'unsupported'
        request = '禁止対象の外部テーブルを削除する要求 PRIVATE_SENTINEL_451'
        result = await self.run_flow(request=request)
        self.assertEqual(result['result'], 'unsupported', result)
        self.assertIsNone(result['data_plan_token'])
        self.assertEqual(self.work.catalog_calls, [])
        self.assertEqual(self.journal()['input']['request_sha256'], H.hashlib.sha256(request.encode()).hexdigest())
        for path in self.local.rglob('*'):
            if path.is_file():
                self.assertNotIn(request.encode(), path.read_bytes())
        repeated = await self.run_flow(resume=True)
        self.assertEqual(repeated['result'], 'unsupported')
        self.assertEqual(self.work.data_calls, 1)

    async def test_failed_data_never_reinvoked_or_curated(self):
        self.work.data_outcome = 'failed'
        self.assertEqual((await self.run_flow())['result'], 'failed')
        self.assertEqual((await self.run_flow(resume=True))['result'], 'failed')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 0))

    async def test_missing_data_result_is_unknown_and_not_reinvoked(self):
        async def missing(*args, **kwargs):
            self.work.data_calls += 1
            return {'result': 'passed'}
        with patch.object(self.work, 'data', missing):
            first = await self.run_flow()
        self.assertEqual(first['result'], 'unknown')
        self.assertEqual((await self.run_flow(resume=True))['result'], 'unknown')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 0))

    async def test_saved_success_after_lost_data_return_is_recovered_without_data_call(self):
        real = self.work.data
        async def interrupted(*args, **kwargs):
            await real(*args, **kwargs)
            raise asyncio.CancelledError()
        with patch.object(self.work, 'data', interrupted), self.assertRaises(asyncio.CancelledError):
            await self.run_flow()
        self.assertEqual(self.journal()['phase'], 'unknown')
        result = await self.run_flow(resume=True)
        self.assertEqual(result['result'], 'passed', result)
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))

    async def test_catalog_partial_failure_resumes_same_source_and_preserves_old_attempt(self):
        self.work.catalog_outcome = 'failed'
        first = await self.run_flow()
        self.assertEqual(first['phase'], 'catalog_failed', first)
        old = H.output_path('catalog-bear', self.work.catalog_calls[0][1])
        hashes = self.all_hashes(old)
        self.work.catalog_outcome = 'passed'
        second = await self.run_flow(resume=True)
        self.assertEqual(second['result'], 'passed', second)
        self.assertEqual(self.work.data_calls, 1)
        self.assertEqual([call[0] for call in self.work.catalog_calls], [first['data_plan_token']] * 2)
        self.assertNotEqual(self.work.catalog_calls[0][1], self.work.catalog_calls[1][1])
        self.assertEqual(self.all_hashes(old), hashes)
        new = H.output_path('catalog-bear', self.work.catalog_calls[1][1])
        self.assertEqual(H.read_json(new / 'apply.json')[0]['writes'], 0)

    async def test_lost_catalog_return_recovers_saved_success_without_reapplication(self):
        real = self.work.curate
        async def interrupted(*args):
            await real(*args)
            raise asyncio.CancelledError()
        with patch.object(self.work, 'curate', interrupted), self.assertRaises(asyncio.CancelledError):
            await self.run_flow()
        result = await self.run_flow(resume=True)
        self.assertEqual(result['result'], 'passed', result)
        self.assertTrue(result['replayed'])
        self.assertEqual(len(self.work.catalog_calls), 1)

    async def test_real_task_cancel_preserves_original_and_does_not_repeat_data(self):
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            self.work.data_calls += 1
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.work, 'data', blocked):
            task = asyncio.create_task(self.run_flow())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.journal()['data']['outcome'], 'interrupted')
        self.assertEqual((await self.run_flow(resume=True))['result'], 'unknown')
        self.assertEqual(self.work.data_calls, 1)

    async def test_lock_rejects_same_handoff_concurrent_execution(self):
        entered, release = asyncio.Event(), asyncio.Event()
        real = self.work.data
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await real(*args, **kwargs)
        with patch.object(self.work, 'data', blocked):
            task = asyncio.create_task(self.run_flow())
            await entered.wait()
            with self.assertRaisesRegex(H.HandoffError, 'HANDOFF_BUSY'):
                await self.run_flow(resume=True)
            release.set()
            self.assertEqual((await task)['result'], 'passed')
        self.assertEqual(self.work.data_calls, 1)

    async def test_initial_intent_failure_means_no_data_and_no_retry_adoption(self):
        with patch.object(H, 'persist', side_effect=OSError('private failure')), self.assertRaises(OSError):
            await self.run_flow()
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (0, 0))
        with self.assertRaises(FileNotFoundError):
            await self.run_flow()
        self.assertEqual(self.work.data_calls, 0)

    async def test_new_directory_parent_fsync_precedes_data_intent(self):
        real, calls = H.sync_directory, []
        def sync(path):
            calls.append(path)
            return real(path)
        with patch.object(H, 'sync_directory', sync):
            result = await self.run_flow()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(calls[:3], [self.local, H.STATE, H.STATE / self.token])

    async def test_parent_directory_fsync_failure_starts_no_child(self):
        with patch.object(H, 'sync_directory', side_effect=OSError('sync failed')), self.assertRaises(OSError):
            await self.run_flow()
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (0, 0))

    async def test_catalog_intent_failure_prevents_catalog_and_resume_never_repeats_data(self):
        real = H.persist
        def fail(directory, journal):
            if journal['phase'] == 'catalog_pending':
                raise OSError('intent failed')
            return real(directory, journal)
        with patch.object(H, 'persist', fail):
            first = await self.run_flow()
        self.assertEqual(first['result'], 'unknown')
        self.assertEqual(len(self.work.catalog_calls), 0)
        self.assertEqual((await self.run_flow(resume=True))['result'], 'passed')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))

    async def test_changed_input_and_invalid_request_stop_before_children(self):
        await self.run_flow()
        with self.assertRaisesRegex(H.HandoffError, 'HANDOFF_INPUT_CONFLICT'):
            await self.run_flow(request='名前の文字数を返して')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))
        self.token = uuid.uuid4().hex
        with self.assertRaises(Exception):
            await self.run_flow(request='bad\x00request')
        self.assertFalse((H.STATE / self.token).exists())

    async def test_source_tamper_prevents_completed_replay_and_any_new_attempt(self):
        result = await self.run_flow()
        source = H.output_path('data-plans', result['data_plan_token'])
        save(source / 'plan.json', {'schema_version': 1, 'source': 'fixture_v1', 'operation': 'row_count'})
        replay = await self.run_flow(resume=True)
        self.assertEqual(replay['result'], 'unknown')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))

    async def test_prior_catalog_receipt_change_blocks_retry(self):
        self.work.catalog_outcome = 'failed'
        await self.run_flow()
        old = H.output_path('catalog-bear', self.work.catalog_calls[0][1])
        result, _ = H.read_json(old / 'result.json')
        result['changed'] = True
        save(old / 'result.json', result)
        replay = await self.run_flow(resume=True)
        self.assertEqual(replay['error_code'], 'CATALOG_ATTEMPT_CHANGED')
        self.assertEqual(len(self.work.catalog_calls), 1)

    async def test_data_token_substitution_does_not_adopt_unrelated_evidence(self):
        await self.run_flow()
        value = self.journal()
        value['source']['run_token'] = uuid.uuid4().hex
        H.persist(H.STATE / self.token, value)
        replay = await self.run_flow(resume=True)
        self.assertEqual(replay['error_code'], 'DATA_SOURCE_CHANGED')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))

    async def test_invalid_journal_identity_and_symlink_are_rejected_before_calls(self):
        await self.run_flow()
        path = H.STATE / self.token / 'handoff.json'
        value = self.journal()
        value['data']['attempt_token'] = '../unrelated'
        save(path, value)
        with self.assertRaises(H.HandoffError):
            await self.run_flow(resume=True)
        path.rename(path.with_name('old.json'))
        path.symlink_to(path.with_name('old.json'))
        with self.assertRaises(H.HandoffError):
            await self.run_flow(resume=True)
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))

    async def test_private_durable_files_and_no_arbitrary_config_or_paths(self):
        await self.run_flow()
        directory = H.STATE / self.token
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / 'handoff.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual((directory / 'handoff.lock').stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ['handoff.json', 'handoff.lock'])

    async def test_wrong_saved_audit_status_prevents_handoff(self):
        real = self.work.data
        async def altered(*args, **kwargs):
            result = await real(*args, **kwargs)
            result['audit_records'][0]['status'] = 'failed'
            save(args[1] / 'result.json', result)
            return result
        with patch.object(self.work, 'data', altered):
            result = await self.run_flow()
        self.assertEqual(result['error_code'], 'INVALID_CHILD_AUDIT')
        self.assertEqual(self.work.catalog_calls, [])

    async def test_catalog_success_with_source_change_is_not_completed(self):
        real = self.work.curate
        async def altered(config, source_token, output):
            result = await real(config, source_token, output)
            source = H.output_path('data-plans', source_token)
            save(source / 'plan.json', {'schema_version': 1, 'source': 'fixture_v1', 'operation': 'row_count'})
            return result
        with patch.object(self.work, 'curate', altered):
            result = await self.run_flow()
        self.assertEqual(result['result'], 'unknown')
        self.assertNotEqual(self.journal()['phase'], 'completed')
        self.assertEqual((await self.run_flow(resume=True))['result'], 'unknown')
        self.assertEqual(len(self.work.catalog_calls), 1)

    async def test_catalog_cancel_resumes_new_attempt_same_source(self):
        entered = asyncio.Event()
        async def blocked(config, source_token, output):
            self.work.catalog_calls.append((source_token, output.name))
            save(output / 'result.json', {'result': 'failed', 'evidence_path': str(output), 'source_run_token': source_token})
            save(output / 'apply-progress.json', {'pending': {'outcome': 'unknown'}})
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.work, 'curate', blocked):
            task = asyncio.create_task(self.run_flow())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        first = self.work.catalog_calls[0]
        before = self.all_hashes(H.output_path('catalog-bear', first[1]))
        result = await self.run_flow(resume=True)
        self.assertEqual(result['result'], 'passed', result)
        self.assertEqual(self.work.data_calls, 1)
        self.assertEqual([call[0] for call in self.work.catalog_calls], [first[0]] * 2)
        self.assertEqual(self.all_hashes(H.output_path('catalog-bear', first[1])), before)


if __name__ == '__main__':
    unittest.main()
