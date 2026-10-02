#!/usr/bin/env python3
"""Opt-in, bounded OpenCode continuation for the dedicated Shirokuma worktree.

No model request is made by the default plan command. Each execute attempt is a
new session; the durable WP/Vault handoff, rather than a stale session context,
supplies continuity. A child timeout is NOT proof that the server stopped work.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / 'tmp/local-experiment/autonomous-runner'
VAULT = (Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/ObsidianVault/Dev/Shirokuma'
         / '06_WorkPackages/062_Local_Development_Handoff.md')
BRANCH = 'local/autonomous-experiment'
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 2048


class RunnerError(RuntimeError):
    """A fixed, non-sensitive stop reason; never include model output."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RunnerError(code)


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RunnerError('GIT_UNAVAILABLE') from None
    require(result.returncode == 0 and len(result.stdout) < 65536, 'GIT_UNAVAILABLE')
    return result.stdout.decode('utf-8', errors='strict').strip()


def checkout_head(root: Path, vault: Path) -> str:
    require(root.is_dir() and not root.is_symlink() and (root / 'AGENTS.md').is_file()
            and (root / 'docs/design/04_Development/04B_Autonomous_Local_Development.md').is_file()
            and vault.is_file() and not vault.is_symlink(), 'SCOPE_UNAVAILABLE')
    require(Path(git(root, 'rev-parse', '--show-toplevel')) == root, 'WRONG_CHECKOUT')
    require(git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD') == BRANCH, 'WRONG_BRANCH')
    require(not git(root, 'status', '--porcelain=v1', '--untracked-files=normal'), 'DIRTY_WORKTREE')
    head = git(root, 'rev-parse', 'HEAD')
    require(re.fullmatch(r'[0-9a-f]{40}', head) is not None, 'HEAD_INVALID')
    return head


def private_directory(path: Path) -> None:
    try:
        if not path.exists():
            path.mkdir(mode=0o700)
        state = path.lstat()
        require(stat.S_ISDIR(state.st_mode) and state.st_uid == os.getuid()
                and stat.S_IMODE(state.st_mode) == 0o700, 'PRIVATE_DIRECTORY_UNSAFE')
    except OSError:
        raise RunnerError('PRIVATE_DIRECTORY_UNSAFE') from None


def lock_directory(root: Path) -> int:
    for path in (root / 'tmp', root / 'tmp/local-experiment', root / 'tmp/local-experiment/autonomous-runner'):
        private_directory(path)
    lock = root / 'tmp/local-experiment/autonomous-runner/runner.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        state = os.fstat(fd)
        require(stat.S_ISREG(state.st_mode) and state.st_uid == os.getuid()
                and stat.S_IMODE(state.st_mode) == 0o600 and state.st_nlink == 1, 'LOCK_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerError('RUNNER_ALREADY_ACTIVE') from None
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_file(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def private_log(path: Path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    return os.fdopen(fd, 'wb', buffering=0)


def save_json(path: Path, value: dict) -> None:
    private_file(path, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())


def prompt(control: dict, vault: Path) -> str:
    return f"""Continue Shirokuma's *local* experimental development in this checkout.
Read AGENTS.md, ../../AGENTS.md, ~/.codex/RTK.md and
docs/design/04_Development/04B_Autonomous_Local_Development.md,
docs/design/06_WorkPackages/061_Local_Experiment_Milestones.md and the private
Vault handoff at {vault} before selecting the next dependency-ready increment.
Read relevant WPs and current lab state; old notes are not fresh observations.
Do not end at one WP/commit/test milestone: continue with other safe independent
work in this invocation. Implement, test, record evidence and gaps, and commit
coherent changes locally. This invocation's model use was explicitly opted in;
do not launch nested paid runs, buy services or create recurring costs. No
GitHub writes/push or publication,
retained data/backup deletion, unknown-request replay or whole-Colima reset.
Do not treat green tests, saved claims or a completion percentage as acceptance.
Do not invoke this runner, launch other autonomous runs or bypass permissions.

This is an opt-in bounded invocation. Its run/time limits are NOT a USD cap.
Before your final answer, write ONE private
0600 JSON checkpoint to the path below (create it with exclusive creation,
do not follow a preexisting entry). Exact keys: schema_version (integer 1),
nonce, iteration (integer), head (full git rev-parse HEAD after your last commit),
decision ("continue" or "stop"), reason ("more_work", "no_safe_work",
"safety_boundary", or "complete"). Use continue/more_work only if more safe
independent tasks remain; otherwise stop with the applicable reason. Never
claim complete merely because tests pass. Do not write secrets to checkpoint.
If interrupted before a trustworthy checkpoint, the runner will stop without
retrying the request. It will also stop on dirty worktree or unchanged HEAD.
CONTROL_JSON={json.dumps(control, sort_keys=True, separators=(',', ':'))}
"""


def unique_pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        require(key not in result, 'CHECKPOINT_INVALID')
        result[key] = value
    return result


def read_checkpoint(path: Path, control: dict, head: str) -> str:
    try:
        state = path.lstat()
        require(stat.S_ISREG(state.st_mode) and state.st_uid == os.getuid() and
                stat.S_IMODE(state.st_mode) == 0o600 and state.st_nlink == 1 and
                0 < state.st_size <= MAX_CHECKPOINT_BYTES, 'CHECKPOINT_INVALID')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(fd)
            require((opened.st_dev, opened.st_ino) == (state.st_dev, state.st_ino), 'CHECKPOINT_INVALID')
            raw = os.read(fd, MAX_CHECKPOINT_BYTES + 1)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
                                  s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(identity(state) == identity(opened) == identity(after) and len(raw) == state.st_size and
                    identity(os.stat(path, follow_symlinks=False)) == identity(state), 'CHECKPOINT_INVALID')
        finally:
            os.close(fd)
        value = json.loads(raw, object_pairs_hook=unique_pairs,
                           parse_constant=lambda _: require(False, 'CHECKPOINT_INVALID'))
        require(type(value) is dict and set(value) == {'schema_version', 'nonce', 'iteration', 'head', 'decision', 'reason'}
                and type(value['schema_version']) is int and value['schema_version'] == 1
                and value['nonce'] == control['nonce']
                and type(value['iteration']) is int and value['iteration'] == control['iteration']
                and value['head'] == head, 'CHECKPOINT_INVALID')
        valid = {('continue', 'more_work'), ('stop', 'no_safe_work'),
                 ('stop', 'safety_boundary'), ('stop', 'complete')}
        require((value['decision'], value['reason']) in valid, 'CHECKPOINT_INVALID')
        return value['reason']
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RunnerError('CHECKPOINT_INVALID') from None


def terminate(process: subprocess.Popen) -> None:
    # This only terminates the local process group. A shared OpenCode server may
    # continue work; never interpret this as remote cancellation or retry it.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED') from None


def invoke(binary: str, root: Path, directory: Path, message: str, deadline: float) -> None:
    command = [binary, 'run', '--format', 'json', message]
    with private_log(directory / 'stdout.jsonl') as stdout, private_log(directory / 'stderr.log') as stderr:
        try:
            process = subprocess.Popen(command, cwd=root, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
        except OSError:
            raise RunnerError('OPENCODE_UNAVAILABLE') from None
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > MAX_LOG_BYTES:
                    terminate(process)
                    raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED')
                time.sleep(0.2)
        except KeyboardInterrupt:
            terminate(process)
            raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED') from None
        require(process.returncode == 0, 'OPENCODE_RUN_FAILED')
        require(os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size <= MAX_LOG_BYTES,
                'OUTPUT_LIMIT_EXCEEDED')


def run(root: Path, vault: Path, binary: str, *, max_runs: int, max_minutes: int) -> tuple[str, Path]:
    require(1 <= max_runs <= 8 and 1 <= max_minutes <= 360, 'BUDGET_INVALID')
    binary = shutil.which(binary)
    require(binary is not None, 'OPENCODE_UNAVAILABLE')
    require(os.environ.get('OPENCODE_DISABLE_PROJECT_CONFIG') != '1', 'PROJECT_INSTRUCTIONS_DISABLED')
    fd = lock_directory(root)
    try:
        checkout_head(root, vault)
        parent = root / 'tmp/local-experiment/autonomous-runner'
        directory = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
        os.chmod(directory, 0o700)
        nonce = uuid.uuid4().hex
        deadline = time.monotonic() + max_minutes * 60
        summary = {'schema_version': 1, 'result': 'stopped', 'attempts': 0, 'reason': None}
        try:
            for number in range(1, max_runs + 1):
                if deadline - time.monotonic() < 10:
                    summary['reason'] = 'time_budget_reached'
                    break
                before = checkout_head(root, vault)
                attempt = directory / f'attempt-{number:03d}'
                attempt.mkdir(mode=0o700)
                control = {'nonce': nonce, 'iteration': number,
                           'checkpoint': str(attempt.relative_to(root) / 'checkpoint.json')}
                print(f'RUN_STARTED attempt={number} evidence={attempt.relative_to(root)}', flush=True)
                summary['attempts'] = number
                invoke(binary, root, attempt, prompt(control, vault), deadline)
                after = checkout_head(root, vault)
                reason = read_checkpoint(attempt / 'checkpoint.json', control, after)
                if after != before:
                    try:
                        ancestor = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', before, after],
                                                  capture_output=True, timeout=20, check=False)
                    except (OSError, subprocess.TimeoutExpired):
                        raise RunnerError('HISTORY_UNVERIFIED') from None
                    require(ancestor.returncode == 0, 'HISTORY_REWRITTEN')
                save_json(attempt / 'result.json', {'head_before': before, 'head_after': after, 'reason': reason})
                print(f'RUN_FINISHED attempt={number} reason={reason}', flush=True)
                if reason != 'more_work':
                    summary['reason'] = 'agent_' + reason + '_claim'
                    break
                require(after != before, 'NO_NEW_COMMIT')
            else:
                summary['reason'] = 'run_budget_reached'
            save_json(directory / 'summary.json', summary)
            return summary['reason'], directory
        except RunnerError as error:
            summary['reason'] = str(error)
            save_json(directory / 'summary.json', summary)
            raise RunnerError(f'{error} evidence={directory.relative_to(root)}') from None
        except KeyboardInterrupt:
            summary['reason'] = 'INTERRUPTED_REMOTE_UNCONFIRMED'
            save_json(directory / 'summary.json', summary)
            raise RunnerError(f'INTERRUPTED_REMOTE_UNCONFIRMED evidence={directory.relative_to(root)}') from None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='opt in to model requests; plan is the default')
    parser.add_argument('--acknowledge-model-charges', action='store_true')
    parser.add_argument('--max-runs', type=int)
    parser.add_argument('--max-minutes', type=int)
    args = parser.parse_args(argv)
    try:
        head = checkout_head(ROOT, VAULT)
        binary = shutil.which('opencode')
        require(binary is not None, 'OPENCODE_UNAVAILABLE')
        if not args.execute:
            require(not args.acknowledge_model_charges, 'BUDGET_INVALID')
            require((args.max_runs is None and args.max_minutes is None) or
                    (args.max_runs is not None and args.max_minutes is not None and
                     1 <= args.max_runs <= 8 and 1 <= args.max_minutes <= 360), 'BUDGET_INVALID')
            print(json.dumps({'mode': 'plan-only', 'head': head, 'branch': BRANCH,
                              'cli': binary, 'active_calls': 0, 'planned_max_runs': args.max_runs,
                              'planned_max_minutes': args.max_minutes,
                              'cost_limit': 'not-enforced'}, sort_keys=True))
            return 0
        require(args.acknowledge_model_charges and args.max_runs is not None and args.max_minutes is not None,
                'BUDGET_REQUIRED')
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            reason, directory = run(ROOT, VAULT, binary, max_runs=args.max_runs, max_minutes=args.max_minutes)
        finally:
            signal.signal(signal.SIGTERM, previous)
        print(json.dumps({'mode': 'executed', 'stop_reason': reason,
                          'evidence': str(directory.relative_to(ROOT)), 'completion_verified': False}, sort_keys=True))
        return 0
    except RunnerError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('INTERRUPTED_REMOTE_UNCONFIRMED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
