"""Offline fake SDK tests; no MLflow DB, HTTP server, model or retained runtime."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import import_evaluations as I

SPEC = importlib.util.spec_from_file_location('evaluation_fixture_helpers', ROOT / 'tests/test_local_evaluation_report.py')
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


class FakeClient:
    def __init__(self):
        self.experiment = None
        self.runs = {}
        self.histories = {}
        self.calls = []
        self.lose_create = False
        self.partial_batch = False
        self.after_batch = None

    def get_experiment_by_name(self, name):
        return self.experiment

    def create_experiment(self, name, *, artifact_location, tags):
        self.calls.append('create_experiment')
        self.experiment = NS(experiment_id='1', name=name, artifact_location=artifact_location,
                             tags=copy.deepcopy(tags), lifecycle_stage='active')
        return '1'

    def get_experiment(self, identity):
        return self.experiment

    def search_runs(self, experiments, **kwargs):
        return list(self.runs.values())

    def create_run(self, experiment, *, start_time, tags):
        self.calls.append('create_run')
        identity = uuid.uuid4().hex
        run = NS(info=NS(run_id=identity, experiment_id=experiment, start_time=start_time,
                        artifact_uri=self.experiment.artifact_location + '/' + identity + '/artifacts',
                        lifecycle_stage='active', status='RUNNING'),
                 data=NS(params={}, metrics={}, tags=copy.deepcopy(tags)))
        self.runs[identity] = run
        self.histories[identity] = {}
        if self.lose_create:
            raise TimeoutError('private create error')
        return copy.deepcopy(run)

    def get_run(self, identity):
        return copy.deepcopy(self.runs[identity])

    def get_metric_history(self, identity, key):
        return [NS(**point) for point in self.histories[identity].get(key, [])]

    def log_batch(self, identity, *, metrics, params, synchronous):
        self.calls.append('log_batch')
        assert synchronous is True
        for row in params:
            self.runs[identity].data.params[row['key']] = row['value']
        for index, metric in enumerate(metrics):
            self.histories[identity].setdefault(metric['key'], []).append(copy.deepcopy(metric))
            self.runs[identity].data.metrics[metric['key']] = metric['value']
            if self.partial_batch and index == 2:
                self.partial_batch = False
                raise TimeoutError('private batch error')
        if self.after_batch:
            self.after_batch()

    def set_terminated(self, identity, *, status):
        self.calls.append('set_terminated')
        self.runs[identity].info.status = status


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.base, self.state, self.artifacts = (self.root / name for name in ('source', 'state', 'artifacts'))
        for path in (self.base, self.state, self.artifacts):
            path.mkdir(mode=0o700)
        for directory, *_ in I.REPORT.SUITES.values():
            (self.base / directory).mkdir(mode=0o700)
        self.saved = {}
        for suite in I.REPORT.SUITES:
            self.saved[suite] = self.save(suite, HELPERS.receipt(suite))
        self.client, self.store_id = FakeClient(), str(uuid.uuid4())

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, suite, value):
        directory, filename, *_ = I.REPORT.SUITES[suite]
        path = self.base / directory / uuid.uuid4().hex
        path.mkdir(mode=0o700)
        self.rewrite(path, suite, value)
        return path

    def rewrite(self, path, suite, value):
        filename = I.REPORT.SUITES[suite][1]
        for target, raw in [(path / filename, json.dumps(value)),
                            (path / 'attempts.jsonl', ''.join(json.dumps(row) + '\n' for row in value.get('attempts', [])))]:
            target.write_text(raw)
            target.chmod(0o600)

    def importer(self, **kwargs):
        return I.Importer(self.client, state=self.state, artifacts=self.artifacts,
                          store_id=self.store_id, base=self.base, **kwargs)

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}

    def test_import_readback_and_repeat_have_zero_logical_writes_and_sources_unchanged(self):
        before = self.snapshot()
        first, output = self.importer().run()
        self.assertEqual(first['result'], 'verified')
        self.assertEqual(first['created_runs'], 6)
        self.assertTrue(first['source_unchanged'])
        self.assertGreater(first['added_metrics'], 0)
        calls = list(self.client.calls)
        repeated, _ = self.importer().run()
        self.assertEqual(repeated['result'], 'verified')
        self.assertEqual(repeated['created_runs'], 0)
        self.assertEqual(repeated['added_metrics'], 0)
        self.assertFalse(any(r['artifact_added'] for r in repeated['runs']))
        self.assertEqual(self.client.calls, calls)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(json.loads((output / 'result.json').read_text()), first)
        self.assertEqual((self.state / 'import-index.json').stat().st_mode & 0o777, 0o600)

    def test_all_eleven_runs_preserve_case_signatures_mock_and_nulls(self):
        self.save('inventory', HELPERS.receipt(mock=True, unknown_tokens=True))
        self.save('sre', HELPERS.receipt('sre', old_sre=True))
        self.save('sre', HELPERS.receipt('sre', platform_sre=True))
        self.save('data', HELPERS.receipt('data', data_variant='requests'))
        self.save('data', HELPERS.receipt('data', data_variant='baseline'))
        result, output = self.importer().run()
        self.assertEqual(result['created_runs'], 11)
        sources = json.loads((output / 'sources.json').read_text())['sources']
        sre = [s for s in sources if s['suite'] == 'sre']
        self.assertEqual({len(s['case_ids']) for s in sre}, {6, 8, 16})
        self.assertEqual(len({s['case_signature'] for s in sre}), 3)
        mock_source = next(s for s in sources if s['provider_kind'] == 'mock')
        row = next(r for r in result['runs'] if r['source_key'] == mock_source['source_key'])
        history = self.client.histories[row['run_id']]
        self.assertNotIn('attempt.prompt_tokens', history)
        self.assertNotIn('prompt_tokens.total', history)
        self.assertNotIn('inference_cost_estimate', history)
        self.assertEqual(history['prompt_tokens.missing_calls'][0]['value'], 10)
        artifact = self.artifacts / 'evaluations' / row['run_id'] / 'artifacts/safe-projection.json'
        saved = json.loads(artifact.read_text())
        self.assertIsNone(saved['inference_cost_estimate'])
        self.assertIsNone(saved['aggregate']['usage']['prompt_tokens']['total'])
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)

    def test_failed_and_incomplete_valid_receipts_are_imported_as_failed_without_fabrication(self):
        self.rewrite(self.saved['inventory'], 'inventory', HELPERS.receipt(failed_index=0))
        self.rewrite(self.saved['sre'], 'sre', HELPERS.receipt('sre', truncate=3, unknown_tokens=True))
        result, output = self.importer().run()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(result['inventory']['coverage']['reported_failed_runs'], 2)
        sources = json.loads((output / 'sources.json').read_text())['sources']
        source = next(s for s in sources if s['suite'] == 'sre')
        self.assertFalse(source['source_complete'])
        self.assertEqual(source['aggregate']['unattempted_inferences'], 29)
        row = next(r for r in result['runs'] if r['source_id'] == source['source_id'])
        self.assertEqual(self.client.runs[row['run_id']].info.status, 'FAILED')
        self.assertEqual(len(self.client.histories[row['run_id']]['attempt.duration_ms']), 3)

    def test_invalid_attempt_log_is_visible_and_stops_all_destination_writes(self):
        (self.saved['sre'] / 'attempts.jsonl').write_text('{}\n')
        importer = self.importer(client_factory=Mock(side_effect=AssertionError('SDK must not initialize')))
        importer.client = None
        result, output = importer.run()
        self.assertEqual(result['failure_code'], 'SOURCE_INVENTORY_INCOMPLETE')
        self.assertEqual(result['inventory']['coverage']['invalid_runs'], 1)
        self.assertEqual(self.client.calls, [])
        self.assertIn('attempt_log_mismatch', (output / 'sources.json').read_text())
        importer.client_factory.assert_not_called()

    def test_duplicate_execution_is_not_hidden_by_source_hash_identity(self):
        original = json.loads((self.saved['inventory'] / 'summary.json').read_text())
        self.save('inventory', original)
        result, _ = self.importer().run()
        self.assertEqual(result['inventory']['coverage']['duplicate_runs'], 2)
        self.assertEqual(self.client.calls, [])

    def test_missing_receipt_and_symlink_remain_visible_without_writes(self):
        (self.saved['sre'] / 'result.json').unlink()
        (self.saved['data'] / 'result.json').unlink()
        (self.saved['data'] / 'result.json').symlink_to(self.saved['catalog'] / 'result.json')
        result, _ = self.importer().run()
        self.assertEqual(result['inventory']['coverage']['incomplete_runs'], 1)
        self.assertEqual(result['inventory']['coverage']['invalid_runs'], 1)
        self.assertEqual(self.client.calls, [])

    def test_changed_valid_source_never_overwrites_existing_import(self):
        self.assertEqual(self.importer().run()[0]['result'], 'verified')
        before = list(self.client.calls)
        self.rewrite(self.saved['inventory'], 'inventory', HELPERS.receipt(failed_index=1))
        result, _ = self.importer().run()
        self.assertEqual(result['failure_code'], 'SOURCE_IDENTITY_CONFLICT')
        self.assertEqual(self.client.calls, before)

    def test_partial_log_batch_retry_reads_history_and_only_adds_missing_points(self):
        self.client.partial_batch = True
        first, _ = self.importer().run()
        self.assertEqual(first['result'], 'failed')
        first_id = next(iter(self.client.runs))
        self.assertEqual(sum(map(len, self.client.histories[first_id].values())), 3)
        repeated, _ = self.importer().run()
        self.assertEqual(repeated['result'], 'verified')
        self.assertEqual(self.client.calls.count('create_run'), 6)
        self.assertEqual(repeated['created_runs'], 5)
        for histories in self.client.histories.values():
            for points in histories.values():
                self.assertEqual(len(points), len({I.canonical(p) for p in points}))

    def test_unknown_create_reply_is_not_adopted_or_recreated(self):
        self.client.lose_create = True
        first, _ = self.importer().run()
        self.assertEqual(first['result'], 'failed')
        self.client.lose_create = False
        repeated, _ = self.importer().run()
        self.assertEqual(repeated['result'], 'failed')
        self.assertEqual(self.client.calls.count('create_run'), 1)
        index = json.loads((self.state / 'import-index.json').read_text())
        self.assertIsNone(next(iter(index['runs'].values()))['run_id'])

    def test_create_intent_failure_prevents_create_run(self):
        original = I.persist
        def fail(path, value):
            if path == self.state / 'import-index.json' and value['runs']:
                raise OSError('private disk error')
            return original(path, value)
        with patch.object(I, 'persist', side_effect=fail):
            result, _ = self.importer().run()
        self.assertEqual(result['result'], 'failed')
        self.assertNotIn('create_run', self.client.calls)

    def test_different_destination_tags_metrics_or_duplicate_history_cannot_be_repaired(self):
        self.importer().run()
        identity = next(iter(self.client.runs))
        initial = copy.deepcopy((self.client.runs, self.client.histories))
        def wrong_tag(): self.client.runs[identity].data.tags['shirokuma.owner'] = 'other'
        def wrong_metric(): next(iter(self.client.histories[identity].values()))[0]['value'] += 1
        def duplicate():
            rows = next(iter(self.client.histories[identity].values()))
            rows.append(copy.deepcopy(rows[0]))
        for change in (wrong_tag, wrong_metric, duplicate):
            self.client.runs, self.client.histories = copy.deepcopy(initial)
            change()
            calls = list(self.client.calls)
            result, _ = self.importer().run()
            self.assertEqual(result['result'], 'failed')
            self.assertEqual(calls, self.client.calls)

    def test_changed_artifact_is_not_overwritten(self):
        self.importer().run()
        identity = next(iter(self.client.runs))
        artifact = self.artifacts / 'evaluations' / identity / 'artifacts/safe-projection.json'
        artifact.write_text('different retained artifact')
        result, _ = self.importer().run()
        self.assertEqual(result['failure_code'], 'ARTIFACT_CONFLICT')
        self.assertEqual(artifact.read_text(), 'different retained artifact')

    def test_artifact_interruption_keeps_final_absent_and_known_run_can_resume(self):
        interrupted = KeyboardInterrupt()
        with patch.object(I.os, 'rename', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.importer().run()
        self.assertIs(caught.exception, interrupted)
        self.assertEqual(list(self.artifacts.rglob('safe-projection.json')), [])
        self.assertEqual(list(self.artifacts.rglob('.pending-*')), [])
        failed = next((self.state / 'imports').glob('*/result.json'))
        self.assertEqual(json.loads(failed.read_text())['failure_code'], 'IMPORT_INTERRUPTED')
        known_id = next(iter(self.client.runs))
        self.assertIsNotNone(next(iter(json.loads((self.state / 'import-index.json').read_text())['runs'].values()))['run_id'])
        result, _ = self.importer().run()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(self.client.calls.count('create_run'), 6)
        self.assertEqual(len([r for r in result['runs'] if r['run_id'] == known_id]), 1)

    def test_completed_artifact_absence_is_tamper_not_silent_repair(self):
        self.importer().run()
        identity = next(iter(self.client.runs))
        artifact = self.artifacts / 'evaluations' / identity / 'artifacts/safe-projection.json'
        artifact.unlink()
        calls = list(self.client.calls)
        result, _ = self.importer().run()
        self.assertEqual(result['failure_code'], 'COMPLETED_ARTIFACT_MISSING')
        self.assertFalse(artifact.exists())
        self.assertEqual(calls, self.client.calls)

    def test_secondary_result_save_failure_cannot_replace_original_cancellation(self):
        interrupted = KeyboardInterrupt()
        persist = I.persist
        def save(path, value):
            if path.name == 'result.json' and value.get('failure_code') == 'IMPORT_INTERRUPTED':
                raise OSError('secondary result save failed')
            return persist(path, value)
        with patch.object(self.client, 'log_batch', side_effect=interrupted), patch.object(I, 'persist', side_effect=save):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.importer().run()
        self.assertIs(caught.exception, interrupted)
        self.assertEqual(self.client.calls.count('create_run'), 1)
        entry = next(iter(json.loads((self.state / 'import-index.json').read_text())['runs'].values()))
        self.assertIsNotNone(entry['run_id'])
        self.assertFalse(entry['completed'])

    def test_artifact_symlink_and_foreign_run_are_refused(self):
        self.importer().run()
        identity = next(iter(self.client.runs))
        artifact = self.artifacts / 'evaluations' / identity / 'artifacts/safe-projection.json'
        artifact.unlink()
        artifact.symlink_to(self.saved['inventory'] / 'summary.json')
        self.assertEqual(self.importer().run()[0]['failure_code'], 'UNSAFE_PRIVATE_FILE')
        self.client.runs['f' * 32] = copy.deepcopy(self.client.runs[identity])
        self.client.runs['f' * 32].info.run_id = 'f' * 32
        self.assertEqual(self.importer().run()[0]['failure_code'], 'UNINDEXED_OR_MISSING_RUN')

    def test_source_drift_after_write_stops_before_next_source_and_does_not_claim_success(self):
        self.client.after_batch = lambda: self.rewrite(self.saved['catalog'], 'catalog', HELPERS.receipt('catalog', failed_index=1))
        result, _ = self.importer().run()
        self.assertEqual(result['failure_code'], 'SOURCE_CHANGED')
        self.assertEqual(self.client.calls.count('create_run'), 3)
        self.assertFalse(result['source_unchanged'])

    def test_guard_failure_blocks_mutations_and_retains_safe_failure(self):
        guard = Mock(side_effect=ValueError('private credential'))
        result, output = self.importer(guard=guard).run()
        self.assertEqual(self.client.calls, [])
        self.assertNotIn('private credential', (output / 'result.json').read_text())

    def test_source_time_is_unknown_and_standard_start_time_is_only_import_time(self):
        with patch.object(I.time, 'time', return_value=2000000000):
            result, output = self.importer().run()
        self.assertEqual(result['result'], 'verified')
        for run in self.client.runs.values():
            self.assertEqual(run.info.start_time, 2000000000000)
            self.assertEqual(run.data.tags['shirokuma.source_started_at'], 'unknown')
            self.assertEqual(run.data.tags['shirokuma.time_semantics'], 'import-time-not-source-execution')
        for source in json.loads((output / 'sources.json').read_text())['sources']:
            self.assertIsNone(source['source_started_at'])

    def test_raw_receipt_text_is_not_copied_into_safe_projection_or_sdk(self):
        value = HELPERS.receipt()
        value.update(prompt='private prompt', sql='private SQL', credential='private credential')
        value['attempts'][0]['raw_response'] = 'private model output'
        self.rewrite(self.saved['inventory'], 'inventory', value)
        result, output = self.importer().run()
        self.assertEqual(result['result'], 'verified')
        for file in [*output.iterdir(), *self.artifacts.rglob('safe-projection.json')]:
            self.assertNotIn('private ', file.read_text())
        self.assertNotIn('private ', str(self.client.runs))


class IsolationTests(unittest.TestCase):
    def test_worker_rejects_inherited_environment_before_sdk_import(self):
        from tools.local_experiment.mlflow import runtime
        with patch.dict(os.environ, {'CLOUD_SECRET': 'not-for-child'}, clear=True), patch.object(runtime, 'store_lock') as lock:
            with self.assertRaisesRegex(I.ImportError, 'NOT_ISOLATED'):
                I.worker()
            lock.assert_not_called()

    def test_parent_launch_uses_fixed_argv_new_environment_and_bounded_timeout(self):
        from tools.local_experiment.mlflow import runtime
        with patch.object(sys, 'argv', ['import_evaluations.py']), patch.object(runtime, 'require_stopped'), \
                patch.object(I.subprocess, 'run', return_value=NS(returncode=0)) as launch:
            self.assertEqual(I.main(), 0)
        args, kwargs = launch.call_args
        self.assertEqual(args[0], [str(runtime.VENV_PYTHON), str(Path(I.__file__).resolve()), '--worker'])
        self.assertEqual(kwargs['env'], runtime.environment())
        self.assertEqual(kwargs['timeout'], 180)
        self.assertNotIn('AWS_ACCESS_KEY_ID', kwargs['env'])


if __name__ == '__main__':
    unittest.main()
