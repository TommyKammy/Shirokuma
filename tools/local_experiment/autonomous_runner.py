#!/usr/bin/env python3
"""Subscription-gated OpenCode continuation for the dedicated Shirokuma worktree.

No model request is made by the default plan command. Each execute attempt is a
new session; the durable WP/Vault handoff, rather than a stale session context,
supplies continuity. A child timeout is NOT proof that the server stopped work.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
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
MAX_HANDOFF_BYTES = 512 * 1024
# The previous process was launched with its own 240-run / seven-day arguments.
# These limits apply only to *future* manual gate batches, never to that process.
MAX_RUNS = 6
MAX_MINUTES = 120
ORIGINAL_WEEK_MINUTES = 7 * 24 * 60
MISSION_BYTES = 4096
MISSION_WP = 'WP-L0-GITOPS-001'
# Owner's 2026-10-07 restart allocation: 100 new starts after the old 500.
SERIES_LIMIT = 600
SOURCE_RUN = 'run-qjiwuw2k'
FIRST_RUN = 'run-3au9fp_4'


def execution_deadline(root: Path) -> float:
    """Owner's Oct10 deadline-only extension, bound to the exact original series.

    No fresh week/allowance or cumulative-limit renewal; unrelated series retain
    their original deadline. Approved endpoint: Oct11 10:30 JST / 01:30 UTC.
    """
    original = (root/'tmp/local-experiment/autonomous-runner'/FIRST_RUN).stat().st_birthtime + ORIGINAL_WEEK_MINUTES*60
    return 1791682200.0 if abs(original - 1791588714.0429842) < 0.001 else original


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


def snapshot_handoff(vault: Path, attempt: Path) -> Path:
    """Copy one stable, owned handoff into the private checkout-scoped attempt.

    Noninteractive V2 clients auto-reject external_directory approval requests;
    do not weaken tool permissions or direct the agent to an inaccessible path.
    """
    try:
        before = vault.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and 0 < before.st_size <= MAX_HANDOFF_BYTES, 'HANDOFF_UNAVAILABLE')
        fd = os.open(vault, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(fd)
            require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), 'HANDOFF_UNAVAILABLE')
            raw = os.read(fd, MAX_HANDOFF_BYTES + 1)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid,
                                  s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(identity(before) == identity(opened) == identity(after)
                    and len(raw) == before.st_size
                    and identity(vault.lstat()) == identity(before), 'HANDOFF_UNAVAILABLE')
        finally:
            os.close(fd)
        path = attempt / 'handoff-snapshot.md'
        private_file(path, raw)
        return path
    except OSError:
        raise RunnerError('HANDOFF_UNAVAILABLE') from None


def save_json(path: Path, value: dict) -> None:
    private_file(path, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())


def mission_file(path: Path, root: Path, head: str) -> dict:
    """Read a short, private, explicit L0 objective; this is not gate evidence."""
    try:
        directory = root / 'tmp/local-experiment/gate-batches'
        parent = directory.lstat()
        require(path.parent == directory and path.name.endswith('.json') and
                stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and
                stat.S_IMODE(parent.st_mode) == 0o700, 'MISSION_INVALID')
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and
                stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
                0 < before.st_size <= MISSION_BYTES, 'MISSION_INVALID')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(fd)
            raw = os.read(fd, MISSION_BYTES + 1)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
                                  s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(identity(before) == identity(opened) == identity(after) ==
                    identity(path.lstat()) and len(raw) == before.st_size, 'MISSION_INVALID')
        finally:
            os.close(fd)
        value = json.loads(raw, object_pairs_hook=unique_pairs,
                           parse_constant=lambda _: require(False, 'MISSION_INVALID'))
        keys = {'schema_version', 'wp_id', 'gate_id', 'head_before', 'baseline',
                'target_evidence', 'allowed_scope', 'stop_conditions', 'recovery'}
        require(type(value) is dict and set(value) == keys and
                type(value['schema_version']) is int and value['schema_version'] == 1 and
                value['wp_id'] == MISSION_WP and
                re.fullmatch(r'L0_[A-Z0-9_]{8,64}', value['gate_id']) is not None and
                value['head_before'] == head, 'MISSION_INVALID')
        for key in ('baseline', 'target_evidence', 'allowed_scope', 'stop_conditions', 'recovery'):
            require(type(value[key]) is str and 30 <= len(value[key]) <= 650 and
                    not any(ord(char) < 32 for char in value[key]), 'MISSION_INVALID')
        return value
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RunnerError('MISSION_INVALID') from None


def private_json(path: Path, code: str) -> dict:
    try:
        state = path.lstat()
        require(stat.S_ISREG(state.st_mode) and state.st_uid == os.getuid() and
                stat.S_IMODE(state.st_mode) == 0o600 and state.st_nlink == 1 and
                0 < state.st_size <= MISSION_BYTES, code)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(fd)
            raw = os.read(fd, MISSION_BYTES + 1)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid,
                                  s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(identity(state) == identity(opened) == identity(after) ==
                    identity(path.lstat()) and len(raw) == state.st_size, code)
        finally:
            os.close(fd)
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        require(type(value) is dict, code)
        return value
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RunnerError(code) from None


def reviewed_safety_stop(root: Path, prior: Path, summary: dict, head: str,
                         repair_only: bool = False) -> bool:
    """Accept a resolved, terminal safety stop for a NEW mission, never replay.

    This is an independently prepared review attestation, not remote proof.
    Missing/failed/unknown sessions still refuse; original evidence is immutable.
    """
    if summary.get('reason') not in ('agent_safety_boundary_claim', 'agent_complete_claim'):
        return False
    number = summary['attempts']
    require(number > 0, 'PRIOR_GATE_RUN_UNVERIFIED')
    last = prior / f'attempt-{number:03d}'
    result = private_json(last / 'result.json', 'PRIOR_GATE_RUN_UNVERIFIED')
    checkpoint = private_json(last / 'checkpoint.json', 'PRIOR_GATE_RUN_UNVERIFIED')
    gate = root / 'tmp/local-experiment/gate-batches'
    diagnostic = repair_only and not (gate / f'review-{prior.name}.json').exists()
    review = private_json(gate / f'{"repair-review" if diagnostic else "review"}-{prior.name}.json',
                          'SAFETY_STOP_NOT_REVIEWED')
    reason = 'complete' if summary['reason'] == 'agent_complete_claim' else 'safety_boundary'
    decision = ('terminal_safety_stop_repair_only_no_replay' if diagnostic else
                'verified_stage_new_mission_no_replay' if reason == 'complete' else
                'blocker_resolved_new_mission_no_replay')
    resolution = review.get('resolution_head')
    require(type(resolution) is str and re.fullmatch(r'[0-9a-f]{40}', resolution) and
            type(result.get('head_after')) is str and
            re.fullmatch(r'[0-9a-f]{40}', result['head_after']) and
            result.get('reason') == reason and
            review.get('schema_version') == 1 and review.get('source_run') == prior.name and
            review.get('original_summary') == summary and
            review.get('original_result') == result and
            review.get('decision') == decision and
            (not diagnostic or (reason == 'safety_boundary' and resolution == head and
             review.get('allowed_gate') == 'L0_OWNER_CHAIN_REPAIR')) and
            review.get('server_outcome') == 'succeeded_idle_nonactive' and
            type(review.get('last_session_id')) is str and
            re.fullmatch(r'ses_[A-Za-z0-9]+', review['last_session_id']) and
            read_checkpoint(last / 'checkpoint.json',
                            {'nonce': checkpoint.get('nonce'), 'iteration': number},
                            result['head_after']) == reason and
            git(root, 'merge-base', '--is-ancestor', result['head_after'], resolution) == '' and
            git(root, 'merge-base', '--is-ancestor', resolution, head) == '',
            'SAFETY_STOP_NOT_REVIEWED')
    return True


def reviewed_transport_stop(root: Path, prior: Path, summary: dict, head: str) -> bool:
    """Separate server reconciliation, not a recreated missing runner result."""
    if summary.get('reason') != 'OPENCODE_RUN_FAILED' or summary.get('attempts') != 1:
        return False
    require(not (prior/'attempt-001/result.json').exists(), 'TRANSPORT_REVIEW_INVALID')
    checkpoint = private_json(prior/'attempt-001/checkpoint.json', 'TRANSPORT_REVIEW_INVALID')
    review = private_json(root/'tmp/local-experiment/gate-batches'/f'transport-review-{prior.name}.json',
                          'TRANSPORT_REVIEW_REQUIRED')
    before = review.get('before_head')
    require(review.get('schema_version') == 1 and review.get('source_run') == prior.name and
            review.get('original_summary') == summary and review.get('checkpoint') == checkpoint and
            review.get('decision') == 'server_success_new_mission_no_replay' and
            review.get('server_outcome') == 'succeeded_idle_nonactive' and
            type(review.get('last_session_id')) is str and
            re.fullmatch(r'ses_[A-Za-z0-9]+', review['last_session_id']) and
            type(before) is str and re.fullmatch(r'[0-9a-f]{40}', before) and
            checkpoint.get('head') != before and
            read_checkpoint(prior/'attempt-001/checkpoint.json',
                            {'nonce': checkpoint.get('nonce'), 'iteration': 1}, checkpoint.get('head'))
            in ('more_work', 'complete') and
            git(root, 'merge-base', '--is-ancestor', before, checkpoint['head']) == '' and
            git(root, 'merge-base', '--is-ancestor', checkpoint['head'], head) == '',
            'TRANSPORT_REVIEW_INVALID')
    return True


def reviewed_independent_stop(root: Path, prior: Path, summary: dict, head: str,
                              next_gate: str | None) -> bool:
    """Leave a no-safe-work scope held; admit only a distinct diagnostic gate."""
    if summary.get('reason') != 'agent_no_safe_work_claim' or next_gate != 'L0_FLUX_FINDINGS_DIAGNOSIS':
        return False
    require(summary.get('attempts') == 1, 'INDEPENDENT_REVIEW_INVALID')
    last = prior/'attempt-001'
    result = private_json(last/'result.json', 'INDEPENDENT_REVIEW_INVALID')
    checkpoint = private_json(last/'checkpoint.json', 'INDEPENDENT_REVIEW_INVALID')
    review = private_json(root/'tmp/local-experiment/gate-batches'/f'independent-review-{prior.name}.json',
                          'INDEPENDENT_REVIEW_REQUIRED')
    reviewed_head = review.get('reviewed_head')
    require(review.get('schema_version') == 1 and review.get('source_run') == prior.name and
            review.get('original_summary') == summary and review.get('original_result') == result and
            review.get('checkpoint') == checkpoint and result.get('reason') == 'no_safe_work' and
            review.get('decision') == 'held_scope_new_independent_diagnosis_no_replay' and
            review.get('held_scope') == 'local_admission/repository_repair' and
            review.get('allowed_gate') == next_gate and
            review.get('server_outcome') == 'succeeded_idle_nonactive' and
            type(review.get('last_session_id')) is str and
            re.fullmatch(r'ses_[A-Za-z0-9]+', review['last_session_id']) and
            type(reviewed_head) is str and re.fullmatch(r'[0-9a-f]{40}', reviewed_head) and
            read_checkpoint(last/'checkpoint.json', {'nonce': checkpoint.get('nonce'), 'iteration': 1},
                            result.get('head_after')) == 'no_safe_work' and
            git(root, 'merge-base', '--is-ancestor', result['head_after'], reviewed_head) == '' and
            git(root, 'merge-base', '--is-ancestor', reviewed_head, head) == '',
            'INDEPENDENT_REVIEW_INVALID')
    return True


def reviewed_refresh_stop(root: Path, prior: Path, summary: dict, head: str,
                          next_gate: str | None) -> bool:
    """Evidence-only capability; original admission/finding scopes remain held."""
    gates = {
        'L0_FLUX_EVIDENCE_REFRESH': ('refresh', ('gate-azr7paf4', 'gate-5d3ur67c')),
        'L0_FLUX_LOCAL_BUILD': ('build', ('gate-azr7paf4', 'gate-5d3ur67c', 'gate-3f5odnk2', 'gate-xezx_z6k')),
    }
    if next_gate not in gates or prior.name not in gates[next_gate][1]:
        return False
    prefix, _ = gates[next_gate]
    require(summary.get('reason') == 'agent_no_safe_work_claim' and summary.get('attempts') == 1,
            'REFRESH_REVIEW_INVALID')
    last = prior/'attempt-001'
    result = private_json(last/'result.json', 'REFRESH_REVIEW_INVALID')
    checkpoint = private_json(last/'checkpoint.json', 'REFRESH_REVIEW_INVALID')
    review = private_json(root/'tmp/local-experiment/gate-batches'/f'{prefix}-review-{prior.name}.json',
                          'REFRESH_REVIEW_REQUIRED')
    reviewed_head = review.get('reviewed_head')
    require(review.get('schema_version') == 1 and review.get('source_run') == prior.name and
            review.get('original_summary') == summary and review.get('original_result') == result and
            review.get('checkpoint') == checkpoint and result.get('reason') == 'no_safe_work' and
            review.get('allowed_gate') == next_gate and
            review.get('decision') == ('held_scope_exact_digest_evidence_refresh_no_replay' if prefix == 'refresh'
                                      else 'held_scope_isolated_source_build_no_replay') and
            (prefix != 'build' or review.get('candidate_summary_sha256') ==
             hashlib.sha256((root/'tmp/local-experiment/flux-pin-candidate/candidate-hn94027w/summary.json').read_bytes()).hexdigest()) and
            (prior.name != 'gate-xezx_z6k' or
             review.get('prior_binary_sha256') == hashlib.sha256(
                 (root/'tmp/local-experiment/flux-local-build/build-aoa39q7w/source-controller').read_bytes()).hexdigest()) and
            review.get('server_outcome') == 'succeeded_idle_nonactive' and
            type(review.get('last_session_id')) is str and
            re.fullmatch(r'ses_[A-Za-z0-9]+', review['last_session_id']) and
            review.get('ledger_sha256') == hashlib.sha256((root/'security/resident-images.json').read_bytes()).hexdigest() and
            type(reviewed_head) is str and re.fullmatch(r'[0-9a-f]{40}', reviewed_head) and
            read_checkpoint(last/'checkpoint.json', {'nonce': checkpoint.get('nonce'), 'iteration': 1},
                            result.get('head_after')) == 'no_safe_work' and
            git(root, 'merge-base', '--is-ancestor', result['head_after'], reviewed_head) == '' and
            git(root, 'merge-base', '--is-ancestor', reviewed_head, head) == '', 'REFRESH_REVIEW_INVALID')
    return True


def gate_preflight(root: Path, head: str, max_runs: int, max_minutes: int,
                   repair_only: bool = False, independent_gate: str | None = None) -> None:
    """Require an independently reviewed boundary; refuse missing/unknown runs.

    ready.json is a local review attestation, NOT independent server or billing
    evidence. Create it only after separately reconciling the specific session.
    """
    base = root / 'tmp/local-experiment/autonomous-runner'
    gate = root / 'tmp/local-experiment/gate-batches'
    try:
        scope = gate.lstat()
        require(stat.S_ISDIR(scope.st_mode) and scope.st_uid == os.getuid() and
                stat.S_IMODE(scope.st_mode) == 0o700, 'GATE_NOT_RECONCILED')
        first = (base / FIRST_RUN).lstat()
        require(stat.S_ISDIR(first.st_mode) and first.st_uid == os.getuid() and
                stat.S_IMODE(first.st_mode) == 0o700 and
                type(getattr(first, 'st_birthtime', None)) is float,
                'ORIGINAL_WEEK_UNVERIFIED')
        require(time.time() + max_minutes * 60 < execution_deadline(root),
                'ORIGINAL_WEEK_EXPIRED')
        hold = private_json(base / 'cap-1000-bridge/bridge-stop.json', 'REVIEW_HOLD_UNVERIFIED')
        require(hold.get('source_run') == SOURCE_RUN and hold.get('reason') == 'POLICY_REVIEW_HOLD',
                'REVIEW_HOLD_UNVERIFIED')
        source = base / SOURCE_RUN
        summary = private_json(source / 'summary.json', 'SOURCE_NOT_CLEAN_BUDGET_BOUNDARY')
        require(summary == {'schema_version': 1, 'result': 'stopped',
                            'reason': 'run_budget_reached', 'attempts': 240} and
                not (source / 'attempt-241').exists(), 'SOURCE_NOT_CLEAN_BUDGET_BOUNDARY')
        final = private_json(source / 'attempt-240/result.json', 'SOURCE_RESULT_UNVERIFIED')
        checkpoint = private_json(source / 'attempt-240/checkpoint.json', 'SOURCE_RESULT_UNVERIFIED')
        ready = private_json(gate / 'ready.json', 'GATE_NOT_RECONCILED')
        source_head = final.get('head_after')
        require(type(source_head) is str and re.fullmatch(r'[0-9a-f]{40}', source_head) and
                final.get('reason') == 'more_work' and
                checkpoint.get('decision') == 'continue' and
                checkpoint.get('reason') == 'more_work' and
                checkpoint.get('head') == source_head and
                checkpoint.get('iteration') == 240 and
                ready == {'schema_version': 1, 'source_run': SOURCE_RUN,
                          'source_head': source_head, 'series_started': 500,
                          'last_session_id': ready.get('last_session_id'),
                          'server_outcome': 'succeeded_idle_nonactive'} and
                type(ready['last_session_id']) is str and
                re.fullmatch(r'ses_[A-Za-z0-9]+', ready['last_session_id']) and
                git(root, 'merge-base', '--is-ancestor', source_head, head) == '',
                'GATE_NOT_RECONCILED')
        started = 500
        for prior in sorted(base.glob('gate-*')):
            state = prior.lstat()
            require(stat.S_ISDIR(state.st_mode) and state.st_uid == os.getuid() and
                    stat.S_IMODE(state.st_mode) == 0o700, 'PRIOR_GATE_RUN_UNVERIFIED')
            result = private_json(prior / 'summary.json', 'PRIOR_GATE_RUN_UNVERIFIED')
            number = result.get('attempts')
            transport = result.get('reason') == 'OPENCODE_RUN_FAILED' and number == 1
            require(type(number) is int and 0 <= number <= MAX_RUNS and
                    result.get('schema_version') == 1 and
                    result.get('result') == 'stopped' and
                    len(list(prior.glob('attempt-*'))) == number and
                    (transport or all((prior / f'attempt-{i:03d}/result.json').exists()
                         for i in range(1, number + 1))), 'PRIOR_GATE_RUN_UNVERIFIED')
            require(result.get('reason') == 'run_budget_reached' or
                    (reviewed_transport_stop(root, prior, result, head) if transport else
                       (reviewed_restart_stop(root, prior, result, head, independent_gate) or
                        reviewed_refresh_stop(root, prior, result, head, independent_gate) or
                       reviewed_independent_stop(root, prior, result, head, independent_gate))
                     if result.get('reason') == 'agent_no_safe_work_claim' else
                     reviewed_safety_stop(root, prior, result, head, repair_only)),
                    'PRIOR_GATE_RUN_UNVERIFIED')
            started += number
        require(started + max_runs <= SERIES_LIMIT, 'SERIES_BUDGET_EXCEEDED')
    except OSError:
        raise RunnerError('GATE_NOT_RECONCILED') from None


def reviewed_restart_stop(root, prior, summary, head, gate):
    """Owner-selected repo-only restart repair; never reopens old build scopes."""
    known = {'gate-azr7paf4', 'gate-5d3ur67c', 'gate-3f5odnk2', 'gate-xezx_z6k', 'gate-ayqdekwa'}
    minimal = gate == 'L0_FLUX_MINIMAL_TEST_REPAIR'
    os_layer = gate == 'L0_KUSTOMIZE_OS_LAYER'
    os_repair = gate in ('L0_KUSTOMIZE_OS_REPAIR', 'L0_KUSTOMIZE_OS_LAYER')
    independent = gate in ('L0_KUSTOMIZE_SOURCE_REPAIR', 'L0_KUSTOMIZE_OS_REPAIR', 'L0_KUSTOMIZE_OS_LAYER')
    planning = gate in ('L0_FLUX_TEST_CAPACITY_PLAN', 'L0_FLUX_MINIMAL_TEST_REPAIR', 'L0_KUSTOMIZE_SOURCE_REPAIR', 'L0_KUSTOMIZE_OS_REPAIR', 'L0_KUSTOMIZE_OS_LAYER')
    if planning:
        known.add('gate-_g0u5c6u')
    if minimal or independent:
        known.add('gate-vpbadha7')
    if independent:
        known.add('gate-ysazsqh6')
    if os_repair:
        known.add('gate-d1i5mq08')
    if os_layer:
        known.add('gate-_zvi8tes')
    if gate not in ('L0_SUPERVISION_CAPACITY_REPAIR', 'L0_FLUX_TEST_CAPACITY_PLAN', 'L0_FLUX_MINIMAL_TEST_REPAIR', 'L0_KUSTOMIZE_SOURCE_REPAIR', 'L0_KUSTOMIZE_OS_REPAIR', 'L0_KUSTOMIZE_OS_LAYER') or prior.name not in known:
        return False
    require(summary.get('reason') == 'agent_no_safe_work_claim' and summary.get('attempts') == 1,
            'RESTART_REVIEW_INVALID')
    last = prior/'attempt-001'
    result = private_json(last/'result.json', 'RESTART_REVIEW_INVALID')
    cp = private_json(last/'checkpoint.json', 'RESTART_REVIEW_INVALID')
    prefix = 'kustomize-os-layer-review' if os_layer else 'kustomize-os-review' if os_repair else 'kustomize-review' if independent else 'minimal-test-review' if minimal else 'test-plan-review' if planning else 'restart-review'
    review = private_json(root/'tmp/local-experiment/gate-batches'/f'{prefix}-{prior.name}.json',
                          'RESTART_REVIEW_REQUIRED')
    if planning:
        evidence = root/'tmp/local-experiment/safe-restart-review/review-mv27dels'
        completed = private_json(evidence/'summary.json', 'RESTART_REVIEW_INVALID')
        pilot = private_json(evidence/'pilot-review.json', 'RESTART_REVIEW_INVALID')
        require(completed.get('classification') == 'RESTART_RESUME_REVIEW_COMPLETED' and
                completed.get('capacity_review') is True and completed.get('worker_replayed') is False and
                pilot.get('classification') == 'API_PACKAGE_TESTS_PASSED' and pilot.get('exit_code') == 0 and
                review.get('completed_review_sha256') == hashlib.sha256((evidence/'summary.json').read_bytes()).hexdigest() and
                review.get('pilot_review_sha256') == hashlib.sha256((evidence/'pilot-review.json').read_bytes()).hexdigest(),
                'RESTART_REVIEW_INVALID')
    if minimal:
        path = root/'tmp/local-experiment/test-capacity-plan/plan-nu7t5m6c/parent-independent-review.json'
        prior_review = private_json(path, 'RESTART_REVIEW_INVALID')
        require(prior_review.get('decision') == 'hold_test_execution_combined_retention_budget' and
                prior_review.get('retained_hashes_match') is True and prior_review.get('profiles_stopped') is True and
                review.get('planning_review_sha256') == hashlib.sha256(path.read_bytes()).hexdigest(),
                'RESTART_REVIEW_INVALID')
    if independent:
        path = root/'tmp/local-experiment/minimal-test-execution/review-6bapyn7_/parent-failure-review.json'
        held = private_json(path, 'RESTART_REVIEW_INVALID')
        require(held.get('operation_outcome') == 'unknown_original_group_receipt_missing' and
                held.get('replay_authorized') is False and held.get('original_hold_preserved') is True and
                held.get('retained_hashes_match') is True and held.get('profiles_stopped') is True and
                review.get('source_operation_hold_sha256') == hashlib.sha256(path.read_bytes()).hexdigest(),
                'RESTART_REVIEW_INVALID')
    if os_repair:
        path = root/'tmp/local-experiment/kustomize-graph/graph-5arqvixh/parent-result-review.json'
        graph = private_json(path, 'RESTART_REVIEW_INVALID')
        require(graph.get('classification') == 'PINNED_PATCH_CHECKSUMS_OFFLINE_GRAPH_VERIFIED' and
                graph.get('metadata_checksums_verified') == 168 and graph.get('unknown_content_snapshot_matches') is True and
                review.get('graph_review_sha256') == hashlib.sha256(path.read_bytes()).hexdigest(),
                'RESTART_REVIEW_INVALID')
    if os_layer:
        path = root/'tmp/local-experiment/kustomize-os-parent-review/review-m9iihc2r/summary.json'
        package = private_json(path, 'RESTART_REVIEW_INVALID')
        require(package.get('classification') == 'PCRE2_ARM64_PACKAGE_AND_CANDIDATE_CLOSURE_VERIFIED' and
                package.get('version') == '10.49-r0' and package.get('image_assembled') is False and
                review.get('os_package_review_sha256') == hashlib.sha256(path.read_bytes()).hexdigest(),
                'RESTART_REVIEW_INVALID')
    reviewed_head = review.get('reviewed_head')
    require(review.get('schema_version') == 1 and review.get('source_run') == prior.name and
            review.get('original_summary') == summary and review.get('original_result') == result and
            review.get('checkpoint') == cp and review.get('allowed_gate') == gate and
            review.get('decision') == ('verified_local_package_inputs_nonactivating_os_layer' if os_layer else
                                       'source_unknown_held_verified_graph_independent_pcre2_repair' if os_repair else
                                       'source_unknown_held_independent_kustomize_source_repair' if independent else
                                       'old_scope_held_minimum_seed_executor_repair_no_replay' if minimal else
                                       'old_scope_held_test_capacity_planning_no_execution' if planning else
                                       'old_scope_held_repository_capacity_repair_no_replay') and
            review.get('server_outcome') == 'succeeded_idle_nonactive' and
            type(review.get('last_session_id')) is str and re.fullmatch(r'ses_[A-Za-z0-9]+',review['last_session_id']) and
            review.get('ledger_sha256') == hashlib.sha256((root/'security/resident-images.json').read_bytes()).hexdigest() and
            type(reviewed_head) is str and re.fullmatch(r'[0-9a-f]{40}',reviewed_head) and
            read_checkpoint(last/'checkpoint.json', {'nonce':cp.get('nonce'),'iteration':1},result.get('head_after'))=='no_safe_work' and
            git(root,'merge-base','--is-ancestor',result['head_after'],reviewed_head)=='' and
            git(root,'merge-base','--is-ancestor',reviewed_head,head)=='','RESTART_REVIEW_INVALID')
    return True


def subscription_preflight(binary: str, model: str, root: Path) -> None:
    """Reject default/other-provider models and missing V2 OpenAI OAuth accounts.

This is not proof of provider billing. Never print the account listing.
"""
    require(re.fullmatch(r'openai/[A-Za-z0-9][A-Za-z0-9._/-]*', model) is not None,
            'SUBSCRIPTION_MODEL_REQUIRED')
    require(not any(os.environ.get(key) for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL')),
            'SUBSCRIPTION_ROUTE_AMBIGUOUS')
    try:
        result = subprocess.run([binary, 'auth', 'list', '--format', 'json'], cwd=root,
                                capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RunnerError('SUBSCRIPTION_AUTH_UNVERIFIED') from None
    require(result.returncode == 0 and len(result.stdout) <= 65536,
            'SUBSCRIPTION_AUTH_UNVERIFIED')
    try:
        integrations = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise RunnerError('SUBSCRIPTION_AUTH_UNVERIFIED') from None
    require(type(integrations) is list, 'SUBSCRIPTION_AUTH_UNVERIFIED')
    accounts = [entry for entry in integrations if type(entry) is dict and entry.get('id') == 'openai']
    require(len(accounts) == 1 and type(accounts[0].get('connections')) is list
            and len(accounts[0]['connections']) == 1
            and type(accounts[0]['connections'][0]) is dict
            and accounts[0]['connections'][0].get('method') == 'oauth',
            'SUBSCRIPTION_AUTH_UNVERIFIED')


def prompt(control: dict, handoff: Path, model: str, mission: dict | None = None) -> str:
    repair = ("\nREPAIR-ONLY CAPABILITY: mac-studio-solo inspection/API-only stage per DEV-04E. "
              "Never start the normal k3s unit, agent, scheduler or controller-manager, "
              "scale Flux above zero, unsuspend sources/Jobs or start application writers. "
              "Only capture/review and inhibit matching secondary owners using UID/version "
              "guards. Unknown mutation or permission rejection ends mutation work.\n"
              if mission and mission['gate_id'] == 'L0_OWNER_CHAIN_REPAIR' else '')
    gate = (f"""This *entire batch* has ONE objective in {mission['wp_id']} ({mission['gate_id']}).
Baseline: {mission['baseline']}
Required evidence: {mission['target_evidence']}
Allowed scope: {mission['allowed_scope']}
Stop conditions: {mission['stop_conditions']}
Recovery: {mission['recovery']}
If a gate cannot safely advance, report a precise blocker and stop; do not
substitute unrelated AgentHouse/FinOps micro-hardening or mark a checkbox to
keep the runner alive. `more_work` may continue *only inside this short batch*;
it does not authorize any later batch. No live/operator acceptance without
fresh, scoped observations.
""" if mission is not None else '')
    return f"""Continue Shirokuma's *local* experimental development in this checkout.
Read AGENTS.md, docs/design/04_Development/04B_Autonomous_Local_Development.md,
docs/design/04_Development/04C_Gate_Driven_Local_Continuation.md and the short
docs/design/04_Development/04D_Current_Gate_Summary.md first. Read relevant
sections of the current WP, Milestones ledger and private Vault handoff snapshot
at {handoff}; do not ingest their entire historical logs as an instruction.
{gate}{repair}The original Vault,
parent workspace AGENTS.md, and ~/.codex are outside this noninteractive CLI's
approved filesystem; do not try to bypass a permission rejection. The root
checkout AGENTS.md and this prompt carry the relevant scope. Prefix shell
commands with rtk. Record current work in repository WPs/Milestones; if direct
Vault update is denied, leave it untouched and document pending sync.
Read the current WP and lab state; old notes are not fresh observations.
Do not turn an isolated commit or green test into a gate pass. Continue only
safe work *for this gate* within this invocation. Implement, test, record
evidence and gaps, and commit coherent changes locally. The owner authorized
the existing ChatGPT Pro weekly allowance; this manually selected short batch
must not silently restart the earlier 500-to-1000 successor. An
unambiguous OpenAI OAuth-only connection and explicit {model} are the local
experimental routing preconditions, NOT independent proof of billing. Do not
stop solely because a provider billing ledger cannot be independently read;
do not claim one was verified. Do not switch providers/accounts or change the
primary model from {model}; do not launch nested opencode runs, buy services
or create recurring costs. If a genuinely difficult technical blocker remains
after focused investigation and the route checks below pass, consult the
project-local read-only `astra-advisor` subagent using `openai/gpt-6-astra`
under the same single OpenAI OAuth connection: at most two Astra calls total
in this parent session,
one initial call and one follow-up on that blocker. Do not start another
consultation for a second blocker. This is the owner's narrow exception,
not permission for a new provider, API key, independent run or automatic
fallback. Before each consultation run
`rtk proxy tools/local_experiment/llm/.venv/bin/python -m tools.local_experiment.astra_preflight`
from this checkout and require
ASTRA_CONSULT_PREFLIGHT_OK; this checks the single OAuth label, absent
API-key route and Astra catalog entry without making a model request.
If it fails or routing is unclear, do not consult.
Give the advisor only sanitized code/test context, never secrets, raw private
logs, Vault contents or saved data. Its advice is not authority to override a
safety stop; verify any recommendation yourself. Its model requests count
against the same weekly allowance but not the runner's session count.
Stop on observed API-key routing, extra charges, missing OAuth, or a
model/provider mismatch. No GitHub writes/push or publication, retained
data/backup deletion, unknown-request replay or whole-Colima reset.
Do not treat green tests, saved claims or a completion percentage as acceptance.
Do not invoke this runner, launch other autonomous runs or bypass permissions.

This is a bounded weekly-limit invocation. Its run/time limits are NOT a USD cap.
Before your final answer, write ONE private
0600 JSON checkpoint to the path below (create it with exclusive creation,
do not follow a preexisting entry). Exact keys: schema_version (integer 1),
nonce, iteration (integer), head (full git rev-parse HEAD after your last commit),
decision ("continue" or "stop"), reason ("more_work", "no_safe_work",
"safety_boundary", or "complete"). Use continue/more_work only if more safe
work for this gate remains *within this batch*; otherwise stop. Never
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
        # The child may have exited between poll and signal. Still reap it;
        # the caller must treat the interrupted request as unconfirmed.
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED') from None
        return
    except PermissionError:
        # In particular, Darwin can reject a group signal after the leader
        # becomes a zombie. Do not turn this into a successful cancellation or
        # leak an unclassified traceback in place of the runner stop reason.
        raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED') from None
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED') from None
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise RunnerError('PROCESS_STOPPED_REMOTE_UNCONFIRMED') from None


def invoke(binary: str, root: Path, directory: Path, message: str, deadline: float, model: str) -> None:
    command = [binary, 'run', '--model', model, '--format', 'json', message]
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


def run(root: Path, vault: Path, binary: str, *, max_runs: int, max_minutes: int,
        model: str, mission: dict | None = None) -> tuple[str, Path]:
    require(1 <= max_runs <= MAX_RUNS and 1 <= max_minutes <= MAX_MINUTES, 'BUDGET_INVALID')
    require(mission is not None, 'MISSION_REQUIRED')
    binary = shutil.which(binary)
    require(binary is not None, 'OPENCODE_UNAVAILABLE')
    require(os.environ.get('OPENCODE_DISABLE_PROJECT_CONFIG') != '1', 'PROJECT_INSTRUCTIONS_DISABLED')
    fd = lock_directory(root)
    try:
        before_batch = checkout_head(root, vault)
        require(mission['head_before'] == before_batch, 'MISSION_HEAD_CHANGED')
        gate_preflight(root, before_batch, max_runs, max_minutes,
                       repair_only=mission['gate_id'] == 'L0_OWNER_CHAIN_REPAIR',
                       independent_gate=mission['gate_id'])
        parent = root / 'tmp/local-experiment/autonomous-runner'
        original_deadline = execution_deadline(root)
        directory = Path(tempfile.mkdtemp(prefix='gate-', dir=parent))
        os.chmod(directory, 0o700)
        nonce = uuid.uuid4().hex
        deadline = time.monotonic() + max_minutes * 60
        summary = {'schema_version': 1, 'result': 'stopped', 'attempts': 0, 'reason': None}
        try:
            for number in range(1, max_runs + 1):
                require(time.time() < original_deadline, 'ORIGINAL_WEEK_EXPIRED')
                if deadline - time.monotonic() < 10:
                    summary['reason'] = 'time_budget_reached'
                    break
                before = checkout_head(root, vault)
                subscription_preflight(binary, model, root)
                attempt = directory / f'attempt-{number:03d}'
                attempt.mkdir(mode=0o700)
                handoff = snapshot_handoff(vault, attempt)
                control = {'nonce': nonce, 'iteration': number,
                           'checkpoint': str(attempt.relative_to(root) / 'checkpoint.json')}
                print(f'RUN_STARTED attempt={number} evidence={attempt.relative_to(root)}', flush=True)
                summary['attempts'] = number
                invoke(binary, root, attempt, prompt(control, handoff.relative_to(root), model, mission), deadline, model)
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
    parser.add_argument('--use-pro-weekly-limit', action='store_true',
                        help='allow repeated calls only after the OpenAI OAuth label check')
    parser.add_argument('--model', default='openai/gpt-6.1-sol',
                        help='session model (default: openai/gpt-6.1-sol)')
    parser.add_argument('--max-runs', type=int)
    parser.add_argument('--max-minutes', type=int)
    parser.add_argument('--mission-file', type=Path,
                        help='private 0600 gate objective bound to the current HEAD (required to execute)')
    args = parser.parse_args(argv)
    try:
        head = checkout_head(ROOT, VAULT)
        binary = shutil.which('opencode')
        require(binary is not None, 'OPENCODE_UNAVAILABLE')
        if not args.execute:
            require(not args.use_pro_weekly_limit, 'BUDGET_INVALID')
            require((args.max_runs is None and args.max_minutes is None) or
                    (args.max_runs is not None and args.max_minutes is not None and
                     1 <= args.max_runs <= MAX_RUNS and 1 <= args.max_minutes <= MAX_MINUTES), 'BUDGET_INVALID')
            preflight = 'not_checked'
            if args.model is not None:
                try:
                    subscription_preflight(binary, args.model, ROOT)
                    preflight = 'openai_oauth_label_present'
                except RunnerError as error:
                    preflight = str(error)
            print(json.dumps({'mode': 'plan-only', 'head': head, 'branch': BRANCH,
                              'cli': binary, 'active_calls': 0, 'planned_max_runs': args.max_runs,
                              'planned_max_minutes': args.max_minutes, 'model': args.model,
                              'subscription_preflight': preflight,
                               'cost_limit': 'not-enforced',
                               'mission_gate': mission_file(args.mission_file, ROOT, head)['gate_id']
                               if args.mission_file is not None else None}, sort_keys=True))
            return 0
        require(args.use_pro_weekly_limit and args.model is not None
                and args.max_runs is not None and args.max_minutes is not None,
                'BUDGET_REQUIRED')
        require(args.mission_file is not None, 'MISSION_REQUIRED')
        mission = mission_file(args.mission_file, ROOT, head)
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            reason, directory = run(ROOT, VAULT, binary, max_runs=args.max_runs,
                                    max_minutes=args.max_minutes, model=args.model,
                                    mission=mission)
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
