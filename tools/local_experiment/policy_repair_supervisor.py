"""Finite repository-only policy snapshot repair. Default makes no model calls."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from tools.local_experiment import restart_supervisor as S
from tools.local_experiment import flux_test_plan as P

R = S.R
GATE = 'L0_POLICY_SNAPSHOT_REPAIR'
PRIOR = S.PRIOR + ('gate-_g0u5c6u', 'gate-vpbadha7', 'gate-ysazsqh6',
                   'gate-d1i5mq08', 'gate-_zvi8tes', 'gate-z31_2ik6')
SCOPES = ('tmp/rv/mis',) + tuple('tmp/local-experiment/' + name for name in (
    'kustomize-source-repair', 'kustomize-graph', 'kustomize-os-repair',
    'kustomize-os-parent-review', 'kustomize-os-layer', 'kustomize-os-image',
    'kustomize-os-scan', 'policy-snapshot-repair'))


def capacity(root):
    total = sum(P.measure(root, path)['charged_bytes'] for path in P.BUILDS)
    for path in SCOPES:
        if (root/path).exists():
            measured = P.measure(root, path)
            total += max(measured['charged_bytes'], measured['copy_bytes'])
    fs = os.statvfs(root)
    R.require(total + 32*P.MIB < 16*P.GIB and fs.f_bavail*fs.f_frsize >= 17*P.GIB,
              'POLICY_REPAIR_CAPACITY_HOLD')
    return total


def tests(root, directory, label):
    # Local decoding/mocked API fixtures only; no runtime activation.
    with R.private_log(directory/(label+'.stdout')) as out, R.private_log(directory/(label+'.stderr')) as err:
        result = subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', 'tests',
                                 '-p', 'test_local_policy.py', '-q'], cwd=root,
                                stdout=out, stderr=err, timeout=120)
    raw = (directory/(label+'.stderr')).read_bytes()
    count = re.search(rb'Ran (\d+) tests', raw)
    failures = re.search(rb'failures=(\d+)', raw)
    errors = re.search(rb'errors=(\d+)', raw)
    value = dict(returncode=result.returncode, tests=int(count[1]) if count else None,
                 failures=int(failures[1]) if failures else 0, errors=int(errors[1]) if errors else 0,
                 stderr_sha256=hashlib.sha256(raw).hexdigest())
    R.save_json(directory/(label+'.json'), value)
    return value


def mission(head, directory, root):
    return dict(schema_version=1, wp_id='WP-L0-GITOPS-001', gate_id=GATE, head_before=head,
        baseline='OS-only pcre2 archive/SBOM completed; dated DB HIGH4 remains unchanged Go dependencies, no runtime activation. Broad policy166 tests fail11/error2 in snapshot fixtures. Parent reproduced baseline. Source original operation unknown/no replay, VM stopped. Deadline2026-10-11T01:30Z, starts522/600.',
        target_evidence='Investigate and fix actual cause of local-policy snapshot test failures without weakening checked reads. Run full test_local_policy.py plus meaningful negative regressions. Fix fixture canonical temp roots if macOS /var symlink is the cause; do not resolve away adversarial links in production. Write '+str((directory/'test-plan.json').relative_to(root))+': schema_version=1, decision=ready_for_parent_review|hold, notes30-350.',
        allowed_scope='Repo-only tests/test_local_policy.py and tools/local_experiment/policy/manage.py if a genuine production defect is demonstrated; matching design/WP/runner notes. Private small diagnostics in this32MiB scope, combined16GiB/free17GiB. Python offline/mock/local kubectl decoding tests only. Preserve denial and runtime security semantics; independently prove intended malicious link/race tests reach their checks.',
        stop_conditions='No VM/runtime/API mutation, policy activation/rollback/admission or pin/ledger change; no Go/compiler/module/image operations, network fetches, source mis access/replay, retained writes/deletion, threshold/counter reset, spending/provider fallback/nested runner. Never bypass permission rejection or soften O_NOFOLLOW/race/size/identity checks. Do not mask failing assertions or remove negative coverage.',
        recovery='Commit coherent root-cause repair and regressions. Parent reviews diff, terminal state, full policy suite, retained hashes/unknown content/stopped profiles and capacity before selecting successor. Preserve original11failures/2errors and unknown/fetch/build holds. Policy suite success is not broad/runtime/admission/L0 acceptance. No owner-per-attempt wait; dependent unsafe actions remain held.')


def run(root, vault):
    parent = root/'tmp/local-experiment/policy-snapshot-repair'
    parent.mkdir(mode=0o700, exist_ok=True)
    from tools.local_experiment.kustomize_source_repair import private_directory
    checked = private_directory(parent); os.close(checked)
    fd = os.open(root/'tmp/local-experiment/gate-supervisor/lock', os.O_RDWR|os.O_NOFOLLOW)
    directory = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        R.require(len(list(parent.glob('repair-*'))) < 2, 'POLICY_REPAIR_APPROACH_EXHAUSTED')
        head = R.checkout_head(root, vault)
        S.S.no_active_worker(root); S.S.check_resume_budget(root, 1, 45)
        R.subscription_preflight('opencode', S.S.MODEL, root)
        capacity(root)
        before = S.retained(root); profiles = S.F.stopped(root); unknown = S.unknown_content(root)
        directory = Path(tempfile.mkdtemp(prefix='repair-', dir=parent))
        R.save_json(directory/'before.json', dict(head=head, retained=before, profiles=profiles,
                    unknown_content_sha256=unknown, started_at=datetime.now(timezone.utc).isoformat()))
        baseline = tests(root, directory, 'baseline-tests')
        R.require(baseline['returncode'] == 1 and baseline['tests'] == 166 and
                  baseline['failures'] == 11 and baseline['errors'] == 2, 'POLICY_BASELINE_CHANGED')
        for name in PRIOR:
            worker = root/'tmp/local-experiment/autonomous-runner'/name
            summary, result, sid = S.S.terminal(root, worker)
            receipt = dict(schema_version=1, source_run=name, original_summary=summary, original_result=result,
                checkpoint=R.private_json(worker/'attempt-001/checkpoint.json', 'CHECKPOINT_INVALID'),
                reviewed_head=head, last_session_id=sid, server_outcome='succeeded_idle_nonactive',
                allowed_gate=GATE, ledger_sha256=before['security/resident-images.json'],
                decision='independent_policy_snapshot_repair_no_runtime_or_replay',
                policy_failure_path=str((directory/'baseline-tests.json').relative_to(root)),
                policy_failure_sha256=hashlib.sha256((directory/'baseline-tests.json').read_bytes()).hexdigest())
            for key, path in (
                ('source_operation_hold_sha256', 'minimal-test-execution/review-6bapyn7_/parent-failure-review.json'),
                ('os_scan_review_sha256', 'kustomize-os-scan/scan-k3vg47w7/summary.json')):
                receipt[key] = hashlib.sha256((root/'tmp/local-experiment'/path).read_bytes()).hexdigest()
            path = root/'tmp/local-experiment/gate-batches'/f'policy-snapshot-review-{name}.json'
            if not path.exists(): R.save_json(path, receipt)
            R.require(R.reviewed_restart_stop(root, worker, summary, head, GATE), 'POLICY_PRIOR_INVALID')
        objective = mission(head, directory, root)
        R.require(all(30 <= len(objective[k]) <= 650 for k in (
            'baseline', 'target_evidence', 'allowed_scope', 'stop_conditions', 'recovery')), 'MISSION_INVALID')
        R.save_json(directory/'mission.json', objective)
        R.gate_preflight(root, head, 1, 45, independent_gate=GATE)
        reason, worker = R.run(root, vault, 'opencode', max_runs=1, max_minutes=45,
                               model=S.S.MODEL, mission=objective)
        summary, result, sid = S.S.terminal(root, worker)
        R.require(reason in ('run_budget_reached', 'agent_complete_claim', 'agent_no_safe_work_claim'),
                  'POLICY_STOP_UNCLASSIFIED')
        after = R.checkout_head(root, vault)
        changed = R.git(root, 'diff', '--name-only', head, after).splitlines()
        R.require(after != head and all(p.startswith('docs/design/') or p in (
            'tests/test_local_policy.py', 'tools/local_experiment/policy/manage.py',
            'tools/local_experiment/AUTONOMOUS_RUNNER.md') for p in changed), 'POLICY_SCOPE_DRIFT')
        R.require(S.retained(root) == before and S.F.stopped(root) == profiles and
                  S.unknown_content(root) == unknown, 'POLICY_RETENTION_DRIFT')
        capacity(root)
        scope = P.measure(root, str(directory.relative_to(root)))
        R.require(max(scope['charged_bytes'], scope['copy_bytes']) < 32*P.MIB, 'POLICY_SCOPE_CAPACITY')
        proposal = S.R.private_json(directory/'test-plan.json', 'POLICY_PROPOSAL_INVALID')
        R.require(proposal.get('schema_version') == 1 and proposal.get('decision') in (
            'ready_for_parent_review', 'hold') and type(proposal.get('notes')) is str and
            30 <= len(proposal['notes']) <= 350, 'POLICY_PROPOSAL_INVALID')
        focused = tests(root, directory, 'parent-tests')
        R.save_json(directory/'summary.json', dict(classification='POLICY_REPAIR_PARENT_REVIEW_PENDING',
            worker=worker.name, head=after, proposal=proposal, policy_tests=focused,
            retained_hashes_match=True, unknown_content_unchanged=True, runtime_unchanged=True,
            automatic_successor=False, gate_acceptance=False))
        print(json.dumps({'evidence':str(directory), 'parent_review_pending':True, 'policy_tests':focused}))
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json', dict(classification='HELD', reason=str(error)[:200],
                                                      operation_replay=False))
        raise
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--use-pro-weekly-limit', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'mode':'plan-only', 'active_calls':0, 'max_minutes':45}))
        return 0
    R.require(args.use_pro_weekly_limit, 'WEEKLY_LIMIT_OPT_IN_REQUIRED')
    run(R.ROOT, R.VAULT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
