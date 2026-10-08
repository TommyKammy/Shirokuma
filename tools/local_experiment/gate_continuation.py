"""Evidence-reviewed continuation after L0 readiness, not a fixed two-step job.

Each single-session mission is reviewed before the next one. Scope is limited
to three remaining L0 dependencies and repository repairs; runtime reconciliation
and retained writers remain inhibited. No arbitrary task/command execution from
worker proposals. Unknown execution and unsafe live observations remain holds.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
import re

from tools.local_experiment import gate_supervisor as S

R = S.R
TOPICS = {'doctor', 'source_identity', 'local_admission', 'flux_findings'}
APPROACHES = {'diagnose', 'repository_repair'}
GATE = 'L0_DEPENDENCY_CONTINUATION'
FINDINGS_GATE = 'L0_FLUX_FINDINGS_DIAGNOSIS'


def proposal(value):
    R.require(isinstance(value, dict) and set(value) == {
        'schema_version', 'topic', 'approach', 'progress', 'next_topic', 'next_approach', 'next_target'},
        'CONTINUATION_REPORT_INVALID')
    R.require(type(value['schema_version']) is int and value['schema_version'] == 1 and
        all(type(value[k]) is str for k in ('topic', 'next_topic', 'approach', 'next_approach', 'progress')) and
        value['topic'] in TOPICS and value['next_topic'] in TOPICS and
        value['approach'] in APPROACHES and value['next_approach'] in APPROACHES and
        value['progress'] in {'measured_blocker', 'functional_change', 'no_progress'} and
        isinstance(value['next_target'], str) and 30 <= len(value['next_target']) <= 350 and
        not any(ord(c) < 32 for c in value['next_target']), 'CONTINUATION_REPORT_INVALID')
    return value


def reserve(root, topic, approach, directory, number):
    R.require(topic in TOPICS and approach in APPROACHES, 'CONTINUATION_TOPIC_INVALID')
    records = root/'tmp/local-experiment/gate-continuation'
    count = 0
    for old in records.glob('series-*/reservation-*.json'):
        v = R.private_json(old, 'CONTINUATION_COUNTER_UNVERIFIED')
        count += v['topic'] == topic and v['approach'] == approach
    R.require(count < 2, 'CONTINUATION_APPROACH_EXHAUSTED')
    R.save_json(directory/f'reservation-{number:03d}.json',
                {'topic': topic, 'approach': approach, 'count': count+1})


def objective(head, topic, approach, target, report):
    risk = (' Exact Flux HIGH finding IDs/affected versions/fixed versions/exposure/freshness analysis only; '
            'no approvals, exceptions, riskDecision change, scan/fetch, policy activation or runtime writes.'
            if topic == 'flux_findings' else '')
    return dict(schema_version=1, wp_id='WP-L0-GITOPS-001', gate_id=FINDINGS_GATE if risk else GATE,
        head_before=head,
        baseline='Node/four pinned Flux controller readiness independently verified; all source/root/child Flux and completed Jobs suspended, retained app owners zero. See DEV-04D/04E. Prior stops and degraded doctor/policy suite remain real evidence.',
        target_evidence=f'{topic}/{approach}: {target} Write private supervisor proposal to {report}. Read contract in tools/local_experiment/gate_continuation.py.'+(' Next proposal stays flux_findings; exhausted scopes remain held.' if risk else ''),
        allowed_scope='WP-L0-GITOPS-001 source identity, doctor protocol or local admission only. Repository implementation/tests and dedicated mac-studio-solo inspection/observation allowed. Before normal node start recheck complete API-only owner/spec/UID inhibition per DEV-04E. Keep existing four pinned Flux controllers at one, sources/children/Jobs suspended and retained app owners zero. NO source redirection/unsuspension, writer start, new runtime resources or policy binding activation in this diagnostic/repository-repair capability.',
        stop_conditions='Unknown operation/session, denied access, owner/retention drift, quota/routing changes or new spending inhibit dependent actions. No nested runner, GitHub writes, public exposure, whole-Colima reset, saved-request replay or retained-data/backup deletion. Parent selects next mission after review.'+risk,
        recovery='Record observations/changes and scoped rollback, preserve all retained disks/PV/PVC/volume identities and failure records. Leave safe node/controller runtime available for independent parent probe. Parent stops dedicated VM on termination only after worker is confirmed idle/non-active. Never restore application counts or unsuspend sources/Jobs automatically.')


def doctor(root, directory, number):
    """A degraded response is evidence, not a transport success or gate pass."""
    try:
        p = subprocess.run(['go', 'run', './cmd/shirokuma', 'doctor', '--profile',
                            'local-experiment', '--output', 'json'], cwd=root,
                           capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise R.RunnerError('DOCTOR_OBSERVATION_UNCONFIRMED') from None
    R.require(len(p.stdout)+len(p.stderr) < R.MAX_LOG_BYTES, 'OUTPUT_LIMIT_EXCEEDED')
    R.private_file(directory/f'doctor-{number:03d}.stdout', p.stdout)
    R.private_file(directory/f'doctor-{number:03d}.stderr', p.stderr)
    R.save_json(directory/f'doctor-{number:03d}.json', {'exit': p.returncode})
    value = json.loads(p.stdout)
    R.require(isinstance(value, dict) and value.get('profile') == 'local-experiment' and
              value.get('context') == 'colima-mac-studio-solo' and
              isinstance(value.get('checks'), list) and value['checks'],
              'DOCTOR_RESPONSE_UNVERIFIED')


def progress(root, before, after, report, directory, number):
    R.require(after != before and R.git(root, 'merge-base', '--is-ancestor', before, after) == '',
              'CONTINUATION_HISTORY_UNVERIFIED')
    if report['progress'] == 'no_progress':
        R.require((report['next_topic'], report['next_approach']) !=
                  (report['topic'], report['approach']), 'NO_PROGRESS_SAME_APPROACH')
    if report['progress'] == 'functional_change':
        files = R.git(root, 'diff', '--name-only', before, after).splitlines()
        R.require(any(p.startswith(('cmd/', 'internal/', 'tools/', 'tests/')) for p in files),
                  'FUNCTIONAL_CHANGE_UNVERIFIED')
        # Fixed parent-side tests, not arbitrary commands supplied by the worker.
        suite = ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_local_bears.py', '-q']
        output = S.command(suite, root, timeout=120)
        R.private_file(directory/f'focused-{number:03d}.log', output)
        output = S.command(['go', 'test', './cmd/shirokuma/...', './internal/...'], root, timeout=120)
        R.private_file(directory/f'go-focused-{number:03d}.log', output)
    # This review certifies a bounded dependency investigation/repair, not L0
    # acceptance. Live readiness and invariant checks are separately mandatory.


def transport_receipt(root, worker, before):
    summary, result, sid = S.terminal(root, worker, expected_before=before)
    R.require(result.get('server_reconciled_only') is True and result['reason'] in ('more_work', 'complete'),
              'TRANSPORT_RECONCILIATION_INVALID')
    return dict(schema_version=1, source_run=worker.name, original_summary=summary,
                checkpoint=R.private_json(worker/'attempt-001/checkpoint.json', 'CHECKPOINT_INVALID'),
                before_head=before, last_session_id=sid, server_outcome='succeeded_idle_nonactive',
                decision='server_success_new_mission_no_replay')


def failed_worker_path(root, reason):
    match = re.fullmatch(r'OPENCODE_RUN_FAILED evidence=tmp/local-experiment/autonomous-runner/(gate-[a-z0-9_]+)', reason)
    R.require(match is not None, 'FAILED_RUN_NOT_RECONCILABLE')
    return root/'tmp/local-experiment/autonomous-runner'/match[1]


def reconcile_transport(root, worker, before, deadline):
    # CLI transport termination is not server cancellation. Wait read-only for
    # this exact server session; never start another worker while it is active.
    while True:
        try:
            return transport_receipt(root, worker, before)
        except R.RunnerError as error:
            if str(error) != 'SESSION_NOT_TERMINAL' or time.time()+5 >= deadline:
                raise
            time.sleep(5)


def resume_review(root, vault, old, directory):
    R.require(old.parent == root/'tmp/local-experiment/gate-continuation' and old.name.startswith('series-'),
              'RESUME_SCOPE_INVALID')
    summary = R.private_json(old/'summary.json', 'RESUME_UNVERIFIED')
    R.require(summary.get('classification') == 'HELD', 'RESUME_UNVERIFIED')
    worker = failed_worker_path(root, summary.get('reason', ''))
    slots = sorted(old.glob('mission-*.json'))
    R.require(slots, 'RESUME_UNVERIFIED')
    number = int(slots[-1].stem.split('-')[-1])
    prior = R.private_json(slots[-1], 'RESUME_UNVERIFIED')
    receipt = transport_receipt(root, worker, prior['head_before'])
    report = proposal(R.private_json(old/f'proposal-{number:03d}.json', 'CONTINUATION_REPORT_INVALID'))
    after = R.checkout_head(root, vault)
    observed = S.probe(root, S.READY, directory, 0)
    doctor(root, directory, 0)
    progress(root, prior['head_before'], after, report, directory, 0)
    R.save_json(directory/'resumed-review.json', {'worker': worker.name, 'proposal': report,
                'live_invariants': observed, 'original_series': old.name, 'no_replay': True})
    path = root/'tmp/local-experiment/gate-batches'/f'transport-review-{worker.name}.json'
    R.require(not path.exists(), 'TRANSPORT_ALREADY_RECONCILED')
    R.save_json(path, receipt)
    return report


def resume_held_review(root, vault, old, worker, directory):
    """Keep exhausted admission work held; select a genuinely different inquiry."""
    R.require(old.parent == root/'tmp/local-experiment/gate-continuation' and old.name.startswith('series-') and
              worker.parent == root/'tmp/local-experiment/autonomous-runner' and worker.name.startswith('gate-'),
              'RESUME_SCOPE_INVALID')
    state = R.private_json(old/'summary.json', 'RESUME_UNVERIFIED')
    R.require(state == {'classification': 'HELD', 'reason': 'STOP_CLASSIFICATION_UNSUPPORTED',
                       'runtime_cleanup_permitted': True}, 'RESUME_UNVERIFIED')
    slots = sorted(old.glob('mission-*.json'))
    R.require(slots, 'RESUME_UNVERIFIED')
    number = int(slots[-1].stem.split('-')[-1])
    prior = R.private_json(slots[-1], 'RESUME_UNVERIFIED')
    report = proposal(R.private_json(old/f'proposal-{number:03d}.json', 'CONTINUATION_REPORT_INVALID'))
    summary, result, sid = S.terminal(root, worker)
    R.require(summary.get('reason') == 'agent_no_safe_work_claim' and
              result['reason'] == 'no_safe_work' and result['head_before'] == prior['head_before'] and
              (report['topic'], report['approach']) == ('local_admission', 'repository_repair'),
              'INDEPENDENT_SCOPE_UNVERIFIED')
    head = R.checkout_head(root, vault)
    R.require(R.git(root, 'merge-base', '--is-ancestor', result['head_after'], head) == '', 'HISTORY_UNVERIFIED')
    observed = S.reopen_ready_state(root, directory)
    doctor(root, directory, 0)
    progress(root, prior['head_before'], head, report, directory, 0)
    return independent_findings_review(root, worker, report, summary, result, sid, head,
                                       directory, observed)


def independent_findings_review(root, worker, report, summary, result, sid, head, directory, observed):
    """Only a proved exhausted contract scope can pivot to exact finding analysis."""
    R.require((report['topic'], report['approach']) == ('local_admission', 'repository_repair') and
              summary.get('reason') == 'agent_no_safe_work_claim' and result['reason'] == 'no_safe_work',
              'INDEPENDENT_SCOPE_UNVERIFIED')
    count = 0
    for path in (root/'tmp/local-experiment/gate-continuation').glob('series-*/reservation-*.json'):
        value = R.private_json(path, 'CONTINUATION_COUNTER_UNVERIFIED')
        count += (value['topic'], value['approach']) == ('local_admission', 'repository_repair')
    R.require(count == 2, 'EXHAUSTED_SCOPE_UNVERIFIED')
    # The report must actually identify findings/freshness, not an arbitrary rename.
    R.require('HIGH' in report['next_target'] and 'freshness' in report['next_target'],
              'NEW_HYPOTHESIS_UNVERIFIED')
    R.save_json(directory/f'independent-review-{worker.name}.json', {'prior_report': report, 'live_invariants': observed,
                'held_scope': 'local_admission/repository_repair',
                'new_hypothesis': 'Exact HIGH IDs, affected/fixed versions, exposure and stale evidence; not another evidence-contract extension',
                'risk_approval': False, 'counter_reset': False})
    R.save_json(root/'tmp/local-experiment/gate-batches'/f'independent-review-{worker.name}.json',
                dict(schema_version=1, source_run=worker.name, original_summary=summary, original_result=result,
                     checkpoint=R.private_json(worker/'attempt-001/checkpoint.json', 'CHECKPOINT_INVALID'),
                     reviewed_head=head, last_session_id=sid, server_outcome='succeeded_idle_nonactive',
                     decision='held_scope_new_independent_diagnosis_no_replay',
                     held_scope='local_admission/repository_repair', allowed_gate=FINDINGS_GATE))
    return dict(report, next_topic='flux_findings', next_approach='diagnose',
                next_target='Analyze the exact retained Flux HIGH finding IDs, affected/fixed package versions, controller exposure and evidence freshness. Produce a concrete pinned-image or evidence-refresh dependency; preserve strict rejection and null riskDecision, no approval/exception/fetch/rescan/activation/runtime mutation.')


def run(root, vault, max_sessions, max_minutes, resume_series=None, held_series=None, held_worker=None):
    R.require(1 <= max_sessions <= 96 and 1 <= max_minutes <= R.ORIGINAL_WEEK_MINUTES,
              'BUDGET_INVALID')
    parent = root/'tmp/local-experiment/gate-continuation'
    parent.mkdir(mode=0o700, exist_ok=True)
    state = parent.lstat()
    R.require(stat.S_ISDIR(state.st_mode) and state.st_uid == os.getuid() and
              stat.S_IMODE(state.st_mode) == 0o700, 'SUPERVISOR_SCOPE_INVALID')
    # Share the L0 parent lock: no two supervision processes can plan/probe concurrently.
    lock = root/'tmp/local-experiment/gate-supervisor/lock'
    fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    directory = None; idle = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = Path(tempfile.mkdtemp(prefix='series-', dir=parent))
        original = (root/'tmp/local-experiment/autonomous-runner'/R.FIRST_RUN).stat().st_birthtime+R.ORIGINAL_WEEK_MINUTES*60
        deadline = min(time.time()+max_minutes*60, original)
        topic, approach = 'doctor', 'diagnose'
        target = 'Reproduce degraded doctor against inhibited live lab; distinguish incomplete MCP/protocol responses from intentional app/Flux suspension and identify one reproducible gate-linked repair.'
        if resume_series is not None:
            report = resume_review(root, vault, resume_series, directory)
            idle = True
            topic, approach, target = report['next_topic'], report['next_approach'], report['next_target']
        if held_series is not None:
            R.require(resume_series is None and held_worker is not None, 'RESUME_SCOPE_INVALID')
            report = resume_held_review(root, vault, held_series, held_worker, directory)
            idle = True
            topic, approach, target = report['next_topic'], report['next_approach'], report['next_target']
        for number in range(1, max_sessions+1):
            minutes = min(120, int((deadline-time.time())//60))
            R.require(minutes >= 1, 'ORIGINAL_WEEK_OR_LOOP_EXPIRED')
            before = R.checkout_head(root, vault)
            S.no_active_worker(root)
            R.subscription_preflight('opencode', S.MODEL, root)
            R.gate_preflight(root, before, 1, minutes,
                            independent_gate=FINDINGS_GATE if topic == 'flux_findings' else None)
            reserve(root, topic, approach, directory, number)
            report_path = directory/f'proposal-{number:03d}.json'
            mission = objective(before, topic, approach, target, str(report_path.relative_to(root)))
            R.require(all(30 <= len(mission[k]) <= 650 for k in
                      ('baseline', 'target_evidence', 'allowed_scope', 'stop_conditions', 'recovery')), 'MISSION_INVALID')
            R.save_json(directory/f'mission-{number:03d}.json', mission)
            idle = False
            receipt = None
            worker_deadline = min(deadline, time.time()+minutes*60)
            try:
                reason, worker = R.run(root, vault, 'opencode', max_runs=1, max_minutes=minutes,
                                       model=S.MODEL, mission=mission)
                S.terminal(root, worker)
            except R.RunnerError as error:
                worker = failed_worker_path(root, str(error))
                receipt = reconcile_transport(root, worker, before, worker_deadline)
                reason = 'server_reconciled_only'
            idle = True
            R.require(reason in ('run_budget_reached', 'agent_complete_claim', 'server_reconciled_only',
                                 'agent_no_safe_work_claim'), 'STOP_CLASSIFICATION_UNSUPPORTED')
            report = proposal(R.private_json(report_path, 'CONTINUATION_REPORT_INVALID'))
            R.require((report['topic'], report['approach']) == (topic, approach), 'CONTINUATION_REPORT_SCOPE_CHANGED')
            R.require(topic != 'flux_findings' or report['next_topic'] == 'flux_findings',
                      'INDEPENDENT_SCOPE_EXPANSION_UNREVIEWED')
            after = R.checkout_head(root, vault)
            observed = S.probe(root, S.READY, directory, number)
            doctor(root, directory, number)
            progress(root, before, after, report, directory, number)
            if reason == 'agent_no_safe_work_claim':
                summary, result, sid = S.terminal(root, worker)
                report = independent_findings_review(root, worker, report, summary, result, sid,
                                                     after, directory, observed)
            R.save_json(directory/f'review-{number:03d}.json', {
                'classification': ('HELD_SCOPE_NEW_INDEPENDENT_DIAGNOSIS' if reason == 'agent_no_safe_work_claim'
                                   else 'REVIEWED_DEPENDENCY_INCREMENT'), 'worker': worker.name,
                'head': after, 'live_invariants': observed, 'proposal': report,
                'gate_acceptance': False})
            if reason == 'agent_complete_claim':
                R.save_json(root/'tmp/local-experiment/gate-batches'/f'review-{worker.name}.json',
                            S.attestation(root, worker, after))
            if receipt is not None:
                R.save_json(root/'tmp/local-experiment/gate-batches'/f'transport-review-{worker.name}.json', receipt)
            topic, approach, target = report['next_topic'], report['next_approach'], report['next_target']
            print(f'CONTINUATION_REVIEWED session={number} next={topic}/{approach}', flush=True)
        R.save_json(directory/'summary.json', {'classification': 'SESSION_ALLOCATION_REACHED', 'sessions': max_sessions})
        return directory
    except (Exception, KeyboardInterrupt) as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json', {'classification': 'HELD',
                'reason': str(error) if isinstance(error, R.RunnerError) else 'UNVERIFIED_STATE',
                'runtime_cleanup_permitted': idle})
        raise
    finally:
        if idle and directory is not None:
            try:
                S.command(['colima', 'stop', S.PROFILE], root, timeout=180)
                R.save_json(directory/'cleanup.json', {'dedicated_vm_stopped': True})
            except R.RunnerError:
                R.save_json(directory/'cleanup.json', {'dedicated_vm_stopped': 'unconfirmed', 'retry': False})
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--use-pro-weekly-limit', action='store_true')
    p.add_argument('--max-sessions', type=int, default=96)
    p.add_argument('--max-minutes', type=int, default=R.ORIGINAL_WEEK_MINUTES)
    p.add_argument('--resume-series', type=Path, help='review a completed transport-failed worker without replay')
    p.add_argument('--resume-held-series', type=Path, help='select independent finding diagnosis after admission scope exhaustion')
    p.add_argument('--held-worker', type=Path)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps({'mode': 'plan-only', 'active_calls': 0, 'topics': sorted(TOPICS),
                          'max_sessions': args.max_sessions, 'max_minutes': args.max_minutes}))
        return 0
    try:
        R.require(args.use_pro_weekly_limit, 'BUDGET_REQUIRED')
        R.require((args.resume_held_series is None) == (args.held_worker is None) and
                  not (args.resume_series is not None and args.resume_held_series is not None),
                  'RESUME_SCOPE_INVALID')
        old = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            d = run(R.ROOT, R.VAULT, args.max_sessions, args.max_minutes, args.resume_series,
                    args.resume_held_series, args.held_worker)
        finally:
            signal.signal(signal.SIGTERM, old)
        print(json.dumps({'evidence': str(d), 'full_acceptance': False}))
        return 0
    except (R.RunnerError, OSError, ValueError, KeyError) as error:
        print(str(error) if isinstance(error, R.RunnerError) else 'CONTINUATION_UNVERIFIED_STATE')
        return 1
    except KeyboardInterrupt:
        print('CONTINUATION_INTERRUPTED_REMOTE_UNCONFIRMED')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
