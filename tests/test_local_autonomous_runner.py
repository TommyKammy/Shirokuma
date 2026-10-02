"""Offline, disposable Git/CLI fixtures; never invoke the actual model."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tools.local_experiment import autonomous_runner as R


FAKE_CLI = '''#!/usr/bin/env python3
import json,os,pathlib,re,subprocess,sys,time
assert sys.argv[1:4]==['run','--format','json']
match=re.search(r'^CONTROL_JSON=(.*)$',sys.argv[4],re.M)
assert match is not None
c=json.loads(match.group(1));root=pathlib.Path.cwd();mode=os.environ.get('FAKE_OPENCODE_MODE','continue')
if mode=='slow':
    time.sleep(20)
if mode=='failure':
    sys.exit(3)
if mode not in ('stop','no_commit'):
    subprocess.run(['git','-C',str(root),'commit','--allow-empty','--quiet','-m','fixture increment'],check=True)
if mode=='dirty':
    (root/'unfinished.txt').write_text('preserve me')
if mode=='missing' or mode=='dirty':
    sys.exit(0)
head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
checkpoint=root/c['checkpoint']
if mode=='symlink':
    checkpoint.symlink_to(root/'AGENTS.md')
    sys.exit(0)
value=dict(schema_version=1,nonce=c['nonce'],iteration=c['iteration'],head=head,
           decision='stop' if mode=='stop' else 'continue',
           reason='no_safe_work' if mode=='stop' else 'more_work')
fd=os.open(checkpoint,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as out:json.dump(value,out)
print(json.dumps({'type':'fixture','iteration':c['iteration']}))
'''


class AutonomousRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=R.ROOT / 'tmp/local-experiment')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout'
        self.root.mkdir(mode=0o700)
        self.vault = Path(self.temp.name) / 'handoff.md'
        self.vault.write_text('saved handoff')
        (self.root / 'AGENTS.md').write_text('fixture instructions')
        docs = self.root / 'docs/design/04_Development'
        docs.mkdir(parents=True)
        (docs / '04B_Autonomous_Local_Development.md').write_text('fixture scope')
        (self.root / '.gitignore').write_text('tmp/\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('checkout', '-q', '-b', R.BRANCH)
        self.git('add', 'AGENTS.md', '.gitignore', 'docs')
        self.git('commit', '-q', '-m', 'fixture baseline')
        self.cli = Path(self.temp.name) / 'fake-opencode'
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o700)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_plan_is_zero_model_calls_and_requires_explicit_budget(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(R.main([]), 0)
            self.assertEqual(json.loads(out.getvalue())['active_calls'], 0)
            self.assertEqual(R.main(['--execute']), 1)
            self.assertIn('BUDGET_REQUIRED', err.getvalue())
        self.assertFalse((self.root/'tmp').exists())

    def test_execute_cli_requires_charge_ack_and_uses_private_handoff(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': 'stop'}), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(R.main(['--execute', '--max-runs', '2', '--max-minutes', '1']), 1)
            self.assertIn('BUDGET_REQUIRED', err.getvalue())
            self.assertEqual(R.main(['--execute', '--acknowledge-model-charges',
                                     '--max-runs', '2', '--max-minutes', '1']), 0)
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1])['completion_verified'], False)
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        output = next(parent.glob('run-*/attempt-001/stdout.jsonl'))
        self.assertIn('fixture', output.read_text())
        self.assertFalse(list(parent.glob('run-*/attempt-002')))

    def test_two_new_sessions_continue_only_after_new_clean_commits(self):
        initial = R.checkout_head(self.root, self.vault)
        with contextlib.redirect_stdout(io.StringIO()):
            reason, directory = R.run(self.root, self.vault, str(self.cli), max_runs=2, max_minutes=1)
        self.assertEqual(reason, 'run_budget_reached')
        self.assertEqual(json.loads((directory/'summary.json').read_text())['attempts'], 2)
        self.assertNotEqual(initial, R.checkout_head(self.root, self.vault))
        self.assertEqual(len(self.git('log', '--oneline', '-2').splitlines()), 2)
        for number in (1, 2):
            attempt = directory / f'attempt-{number:03d}'
            self.assertEqual(json.loads((attempt/'result.json').read_text())['reason'], 'more_work')
            self.assertEqual(attempt.stat().st_mode & 0o777, 0o700)
            for name in ('checkpoint.json', 'stdout.jsonl', 'stderr.log', 'result.json'):
                self.assertEqual((attempt/name).stat().st_mode & 0o777, 0o600)

    def test_agent_stop_claim_never_becomes_verified_completion(self):
        with mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': 'stop'}), \
             contextlib.redirect_stdout(io.StringIO()):
            reason, directory = R.run(self.root, self.vault, str(self.cli), max_runs=3, max_minutes=1)
        self.assertEqual(reason, 'agent_no_safe_work_claim')
        self.assertEqual(json.loads((directory/'summary.json').read_text())['attempts'], 1)
        self.assertFalse((directory/'attempt-002').exists())

    def test_missing_or_symlink_checkpoint_and_no_commit_fail_closed(self):
        for mode, code in [('missing', 'CHECKPOINT_INVALID'), ('symlink', 'CHECKPOINT_INVALID'),
                           ('no_commit', 'NO_NEW_COMMIT')]:
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': mode}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(R.RunnerError, code):
                    R.run(self.root, self.vault, str(self.cli), max_runs=2, max_minutes=1)
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        self.assertEqual(len(list(parent.glob('run-*/summary.json'))), 3)
        self.assertFalse(list(parent.glob('run-*/attempt-002')))

    def test_dirty_or_nonzero_exit_preserves_evidence_and_never_retries(self):
        for mode, code in [('dirty', 'DIRTY_WORKTREE'), ('failure', 'OPENCODE_RUN_FAILED')]:
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': mode}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(R.RunnerError, code):
                    R.run(self.root, self.vault, str(self.cli), max_runs=2, max_minutes=1)
            if mode == 'dirty':
                self.assertEqual((self.root/'unfinished.txt').read_text(), 'preserve me')
                # Clear only this test-owned disposable file for the next fixture.
                (self.root/'unfinished.txt').unlink()
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        self.assertFalse(list(parent.glob('run-*/attempt-002')))

    def test_scope_lock_and_timeout_do_not_launch_another_session(self):
        self.git('checkout', '-q', '-b', 'wrong-branch')
        with self.assertRaisesRegex(R.RunnerError, 'WRONG_BRANCH'):
            R.checkout_head(self.root, self.vault)
        self.git('checkout', '-q', R.BRANCH)
        fd = R.lock_directory(self.root)
        try:
            with self.assertRaisesRegex(R.RunnerError, 'RUNNER_ALREADY_ACTIVE'):
                R.lock_directory(self.root)
        finally:
            os.close(fd)
        attempt = self.root/'tmp/local-experiment/autonomous-runner/timeout-fixture'
        attempt.mkdir(mode=0o700)
        with mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': 'slow'}):
            with self.assertRaisesRegex(R.RunnerError, 'PROCESS_STOPPED_REMOTE_UNCONFIRMED'):
                R.invoke(str(self.cli), self.root, attempt,
                         R.prompt({'nonce': 'a'*32, 'iteration': 1, 'checkpoint': 'unused'}, self.vault),
                         time.monotonic()+0.5)
        self.assertTrue((attempt/'stdout.jsonl').exists())

    def test_checkpoint_rejects_duplicate_stale_and_nonprivate_entries(self):
        path = Path(self.temp.name)/'checkpoint.json'
        control = {'nonce': 'a'*32, 'iteration': 1, 'checkpoint': 'unused'}
        head = self.git('rev-parse', 'HEAD')
        correct = dict(schema_version=1, nonce=control['nonce'], iteration=1,
                       head=head, decision='stop', reason='no_safe_work')
        path.write_text(json.dumps(correct))
        path.chmod(0o600)
        self.assertEqual(R.read_checkpoint(path, control, head), 'no_safe_work')
        for bad in (dict(correct, nonce='b'*32), dict(correct, iteration=True),
                    dict(correct, head='0'*40), dict(correct, reason='more_work')):
            with self.subTest(bad=bad):
                path.write_text(json.dumps(bad))
                with self.assertRaisesRegex(R.RunnerError, 'CHECKPOINT_INVALID'):
                    R.read_checkpoint(path, control, head)
        path.write_text(json.dumps(correct)[:-1]+',"nonce":"'+control['nonce']+'"}')
        with self.assertRaisesRegex(R.RunnerError, 'CHECKPOINT_INVALID'):
            R.read_checkpoint(path, control, head)
        path.write_text(json.dumps(correct))
        path.chmod(0o644)
        with self.assertRaisesRegex(R.RunnerError, 'CHECKPOINT_INVALID'):
            R.read_checkpoint(path, control, head)

    def test_unreaped_local_process_is_never_confirmed_stopped(self):
        process = mock.Mock(pid=123)
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 3),
                                    subprocess.TimeoutExpired('fixture', 3)]
        with mock.patch.object(R.os, 'killpg') as kill:
            with self.assertRaisesRegex(R.RunnerError, 'PROCESS_STOPPED_REMOTE_UNCONFIRMED'):
                R.terminate(process)
        self.assertEqual(kill.call_count, 2)


if __name__ == '__main__':
    unittest.main()
