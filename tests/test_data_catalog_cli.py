"""Dedicated CLI suite, deliberately outside the existing Data Bear glob."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
import fcntl
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
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment/bears'))
import data_catalog_cli as CLI

H = CLI.H
TOKEN = '1' * 32
DATA = '2' * 32
PLAN = '3' * 32
RAW = '名前の文字数を取得 PRIVATE_REQUEST_SENTINEL_412'


def saved(result='passed'):
    return {'handoff_token': TOKEN, 'result': result,
        'phase': 'completed' if result == 'passed' else 'unsupported' if result == 'unsupported' else 'unknown',
        'data_attempt_token': DATA, 'data_plan_token': PLAN if result == 'passed' else None,
        'catalog_attempts': 1 if result == 'passed' else 0, 'replayed': False,
        'error_code': None, 'evidence': '/private/absolute/path-must-not-be-forwarded'}


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'data-catalog'
        self.calls = []
        d = SimpleNamespace(load_config=lambda _: {'active_provider': 'local'}, validate_config=lambda _: None,
            request_spec=lambda **kwargs: kwargs, SESSION=SimpleNamespace(interruption_handlers=nullcontext))
        p = patch.object(H, 'modules', return_value=(d, None, None))
        self.modules = p.start(); self.addCleanup(p.stop)
        p = patch.object(H, 'STATE', self.state)
        p.start(); self.addCleanup(p.stop)
        async def invoke(config, identity, **kwargs):
            self.calls.append((identity, kwargs))
            return saved()
        p = patch.object(H, 'run_handoff', side_effect=invoke)
        self.invoke = p.start(); self.addCleanup(p.stop)

    def main(self, args, data=b''):
        out = io.StringIO()
        code = CLI.main(args, stdin=io.BytesIO(data), stdout=out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertTrue(1 <= len(lines) <= 2)
        self.assertEqual(set(lines[-1]), CLI.FINAL_KEYS)
        for line in lines:
            self.assertNotIn(RAW, json.dumps(line, ensure_ascii=False))
        return code, lines

    def test_run_forwards_stdin_only_and_strict_safe_projection(self):
        code, lines = self.main(['run', '--handoff-token', TOKEN, '--request-stdin'], (RAW+'\r\n').encode())
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], {'schema_version': 1, 'event': 'allocated', 'command': 'run', 'handoff_token': TOKEN})
        self.assertEqual(self.calls, [(TOKEN, {'case_id': None, 'request': RAW, 'resume': False})])
        self.assertEqual(lines[-1]['evidence'], 'tmp/local-experiment/data-catalog/'+TOKEN)
        self.assertEqual(lines[-1]['execution'], 'invoked')
        self.assertFalse(list(self.root.rglob('*')))

    def test_input_rejections_before_any_module_or_workflow(self):
        for data in (b'', b' ', b'\xff', b'a'*513, b'a'*4097, b'x\ny', b'x\x00', b'x\xc2\x85', '😀'.encode()*513):
            self.modules.reset_mock()
            code, lines = self.main(['run', '--handoff-token', TOKEN, '--request-stdin'], data)
            self.assertEqual(code, 1)
            self.assertEqual(lines[-1]['result'], 'not_started')
            self.assertEqual(lines[-1]['error_code'], 'INPUT_INVALID')
            self.modules.assert_not_called()
        self.invoke.assert_not_called()

    def test_arguments_rejected_without_input_echo_or_execution(self):
        for args in (['run', '--handoff-token', TOKEN], ['run', '--handoff-token', TOKEN, '--case', RAW],
                     ['run', '--handoff-token', TOKEN, '--request', RAW],
                     ['run', '--handoff-token', '../wrong', '--case', 'row_count_ja'],
                     ['resume', '--handoff-token', TOKEN, '--request-stdin']):
            code, lines = self.main(args)
            self.assertEqual(code, 1)
            self.assertEqual(lines[-1]['result'], 'not_started')
        self.modules.assert_not_called(); self.invoke.assert_not_called()

    def test_environment_failure_is_not_started_without_raw_exception(self):
        self.modules.side_effect = ImportError(RAW)
        code, lines = self.main(['run', '--handoff-token', TOKEN, '--case', 'row_count_ja'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['result'], 'not_started')
        self.assertEqual(lines[-1]['error_code'], 'ENVIRONMENT_NOT_READY')
        self.invoke.assert_not_called()

    def test_library_stdout_stderr_and_exception_are_not_forwarded(self):
        async def noisy(*a, **kw):
            print(RAW)
            print(RAW, file=sys.stderr)
            raise RuntimeError(RAW)
        self.invoke.side_effect = noisy
        code, lines = self.main(['run', '--handoff-token', TOKEN, '--request-stdin'], RAW.encode())
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['result'], 'unknown')
        self.assertEqual(lines[-1]['execution'], 'invoked')
        self.assertEqual(lines[-1]['error_code'], 'HANDOFF_FAILED')

    def test_unsupported_exit_two(self):
        async def refuse(*a, **kw): return saved('unsupported')
        self.invoke.side_effect = refuse
        code, lines = self.main(['run', '--handoff-token', TOKEN, '--request-stdin'], RAW.encode())
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1]['result'], 'unsupported')
        self.assertIsNone(lines[-1]['data_plan_token'])

    def test_missing_resume_never_loads_models(self):
        code, lines = self.main(['resume', '--handoff-token', TOKEN])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['result'], 'not_started')
        self.assertEqual(lines[-1]['error_code'], 'HANDOFF_NOT_FOUND')
        self.modules.assert_not_called(); self.invoke.assert_not_called()

    def test_resume_has_no_request_and_uses_existing_token(self):
        (self.state / TOKEN).mkdir(parents=True)
        code, lines = self.main(['resume', '--handoff-token', TOKEN])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [(TOKEN, {'case_id': None, 'request': None, 'resume': True})])

    def test_cancel_has_unknown_result_and_interrupted_exit(self):
        async def cancel(*a, **kw): raise asyncio.CancelledError()
        self.invoke.side_effect = cancel
        code, lines = self.main(['run', '--handoff-token', TOKEN, '--case', 'row_count_ja'])
        self.assertEqual(code, 130)
        self.assertEqual(lines[-1]['result'], 'unknown')
        self.assertEqual(lines[-1]['error_code'], 'INTERRUPTED')

    def test_child_mismatched_token_or_success_phase_never_passes(self):
        for value in (dict(saved(), handoff_token='4'*32), dict(saved(), phase='failed')):
            async def invalid(*a, **kw): return value
            self.invoke.side_effect = invalid
            code, lines = self.main(['run', '--handoff-token', TOKEN, '--case', 'row_count_ja'])
            self.assertEqual(code, 1)
            self.assertEqual(lines[-1]['result'], 'unknown')
            self.assertEqual(lines[-1]['error_code'], 'HANDOFF_EVIDENCE_INVALID')

    def test_not_found_status_is_pure_and_does_not_create_state(self):
        code, lines = self.main(['status', '--handoff-token', TOKEN])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['result'], 'not_found')
        self.assertEqual(lines[-1]['execution'], 'read-only')
        self.modules.assert_not_called(); self.invoke.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_broken_symlink_is_not_reported_as_absent(self):
        self.state.symlink_to(self.root / 'nonexistent', target_is_directory=True)
        code, lines = self.main(['status', '--handoff-token', TOKEN])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['result'], 'unknown')
        self.invoke.assert_not_called()

    def test_isolated_script_entrypoint_status_requires_no_optional_packages(self):
        process = subprocess.run([sys.executable, '-I', '-B', str(Path(CLI.__file__)),
            'status', '--handoff-token', '0'*32], capture_output=True, text=True, timeout=10,
            cwd=self.root, env={k: v for k, v in os.environ.items() if k in ('PATH','HOME','TMPDIR','USER','LANG')})
        self.assertEqual(process.returncode, 1, process.stderr)
        lines = [json.loads(x) for x in process.stdout.splitlines()]
        self.assertEqual(lines[-1]['result'], 'not_found')
        self.assertEqual(set(lines[-1]), CLI.FINAL_KEYS)


HAS_DEPS = importlib.util.find_spec('langgraph') is not None


@unittest.skipUnless(HAS_DEPS, 'requires the dedicated pinned LLM environment')
class SavedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / 'tests'))
        import test_local_data_catalog as F
        self.F = F
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.local = self.root / 'tmp/local-experiment'
        self.local.mkdir(mode=0o700, parents=True)
        self.token = uuid.uuid4().hex
        self.config = F.D.load_config(ROOT / 'tools/local_experiment/llm/providers.qwen-local.json')
        for owner, name, value in ((H, 'ROOT', self.root), (H, 'STATE', self.local / 'data-catalog'),
                (F.D, 'ROOT', self.root), (F.E, 'EVIDENCE_ROOT', self.local / 'data-plans')):
            p = patch.object(owner, name, value);p.start();self.addCleanup(p.stop)
        self.work = F.FakeWorkflows(self.root)

    async def run_flow(self):
        return await H.run_handoff(self.config, self.token, request=RAW,
            data_runner=self.work.data, catalog_runner=self.work.curate)

    def snapshot(self):
        import hashlib
        return {str(p.relative_to(self.local)): (hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mode, p.stat().st_mtime_ns) for p in self.local.rglob('*') if p.is_file()}

    def inspect(self):
        with patch.object(H, 'run_handoff', side_effect=AssertionError('status invoked workflow')):
            return CLI.inspect_handoff(self.token)

    async def test_completed_status_deeply_verifies_saved_artifacts_without_changes(self):
        self.assertEqual((await self.run_flow())['result'], 'passed')
        before = self.snapshot()
        view, code = self.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(view['result'], 'passed')
        self.assertEqual(view['verification'], 'saved-evidence')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))
        self.assertEqual(self.snapshot(), before)

    async def test_tampered_completed_evidence_is_unknown_not_passed(self):
        await self.run_flow()
        journal, _ = H.read_json(H.STATE / self.token / 'handoff.json')
        p = H.output_path('catalog-bear', journal['catalog_attempts'][-1]['attempt_token']) / 'spans.jsonl'
        p.write_text('{}\n')
        view, code = self.inspect()
        self.assertEqual(code, 1)
        self.assertEqual(view['result'], 'unknown')
        self.assertEqual(view['error_code'], 'HANDOFF_EVIDENCE_INVALID')

    async def test_unsupported_and_failed_sources_are_not_success(self):
        for outcome in ('unsupported', 'failed'):
            self.token = uuid.uuid4().hex
            self.work.data_outcome = outcome
            await self.run_flow()
            before = self.snapshot()
            view, code = self.inspect()
            self.assertEqual(code, 0)  # Successful inspection, not workflow success.
            self.assertEqual(view['result'], outcome)
            self.assertEqual(view['verification'], 'saved-evidence')
            self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.work.catalog_calls)

    async def test_pending_intent_without_result_remains_unknown_and_is_not_restarted(self):
        async def missing(*a, **kw): return {'result': 'passed'}
        with patch.object(self.work, 'data', missing):
            result = await self.run_flow()
        self.assertEqual(result['result'], 'unknown')
        before = self.snapshot()
        view, code = self.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(view['result'], 'unknown')
        self.assertEqual(view['verification'], 'journal-only')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.work.catalog_calls)

    async def test_active_lock_reports_busy_without_attempt_or_deep_child_reads(self):
        await self.run_flow()
        before = self.snapshot()
        with (H.STATE / self.token / 'handoff.lock').open('rb') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(H, 'data_evidence', side_effect=AssertionError('busy status read child')):
                view, code = self.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(view['result'], 'busy')
        self.assertEqual(view['verification'], 'journal-only')
        self.assertEqual(self.snapshot(), before)

    async def test_missing_lock_never_created_by_status(self):
        await self.run_flow()
        path = H.STATE / self.token / 'handoff.lock'
        path.unlink()
        view, code = self.inspect()
        self.assertEqual(code, 1)
        self.assertFalse(path.exists())

    async def test_wrong_journal_top_level_is_safe_and_nonmutating(self):
        await self.run_flow()
        self.F.save(H.STATE / self.token / 'handoff.json', [])
        before = self.snapshot()
        view, code = self.inspect()
        self.assertEqual(code, 1)
        self.assertEqual(view['result'], 'unknown')
        self.assertEqual(view['error_code'], 'HANDOFF_JOURNAL_INVALID')
        self.assertEqual(self.snapshot(), before)

    async def test_catalog_failure_saved_receipts_can_be_inspected_without_retry(self):
        self.work.catalog_outcome = 'failed'
        self.assertEqual((await self.run_flow())['result'], 'failed')
        before = self.snapshot()
        view, code = self.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(view['result'], 'failed')
        self.assertEqual(view['phase'], 'catalog_failed')
        self.assertEqual(view['verification'], 'saved-evidence')
        self.assertEqual((self.work.data_calls, len(self.work.catalog_calls)), (1, 1))
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
