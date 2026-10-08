"""One bounded evidence-refresh mission, with no VM/container or policy writes."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import os
import stat

from tools.local_experiment import gate_continuation as C

R, S = C.R, C.S
GATE = 'L0_FLUX_EVIDENCE_REFRESH'
PRIOR = ('gate-azr7paf4', 'gate-5d3ur67c')


def stopped(root):
    value = [json.loads(line) for line in S.command(['colima', 'list', '--json'], root).splitlines()]
    R.require(any(p.get('name') == S.PROFILE and p.get('status') == 'Stopped' for p in value),
              'REFRESH_VM_NOT_STOPPED')
    return value


def inputs(root):
    ledger = root/'security/resident-images.json'
    images = [x for x in json.loads(ledger.read_bytes())['images'] if x['component'] in S.FLUX]
    R.require(len(images) == 4 and {x['component'] for x in images} == S.FLUX and
              all(x['platform'] == 'linux/arm64' and '@sha256:' in x['reference'] for x in images),
              'REFRESH_INPUT_UNVERIFIED')
    return images, hashlib.sha256(ledger.read_bytes()).hexdigest()


def verify_scans(directory, images, started):
    """New evidence can pass identity/freshness while vulnerability admission fails."""
    version = R.private_json(directory/'scanner-version.json', 'REFRESH_SCANNER_UNVERIFIED')
    db = version.get('VulnerabilityDB', {})
    def stamp(value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        R.require(parsed.tzinfo is not None, 'REFRESH_TIMESTAMP_UNVERIFIED')
        return parsed.timestamp()
    now = time.time()
    R.require(isinstance(version.get('Version'), str) and
              -120 <= now-stamp(db.get('UpdatedAt')) <= 48*3600,
              'REFRESH_DB_NOT_CURRENT')
    observed = []
    for image in images:
        path = directory/(image['component']+'.trivy.json')
        report = S.baseline_file(path)
        R.require(isinstance(report, dict), 'REFRESH_SCAN_UNVERIFIED')
        meta = report.get('Metadata', {})
        config = meta.get('ImageConfig', {})
        R.require(report.get('ArtifactName') == image['reference'] and
                  image['reference'] in meta.get('RepoDigests', []) and
                  config.get('architecture') == 'arm64' and config.get('os') == 'linux' and
                  report.get('Trivy', {}).get('Version') == version['Version'] and
                  started-120 <= stamp(report.get('CreatedAt')) <= now+120 and
                  isinstance(report.get('Results'), list) and report['Results'],
                  'REFRESH_SCAN_IDENTITY_UNVERIFIED')
        findings = [v for result in report['Results'] for v in result.get('Vulnerabilities', [])]
        observed.append({'component': image['component'], 'reference': image['reference'],
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'high': sum(v.get('Severity') == 'HIGH' for v in findings),
            'critical': sum(v.get('Severity') == 'CRITICAL' for v in findings)})
    return {'scans': observed, 'scanner_version': version['Version'], 'db': db,
            'evidence_refresh_verified': True, 'admission': False, 'risk_approval': False}


def mission(head, directory):
    target = str(directory.relative_to(R.ROOT))
    return dict(schema_version=1, wp_id='WP-L0-GITOPS-001', gate_id=GATE, head_before=head,
        baseline='Exact retained four ARM64 Flux digests have 25 HIGH occurrences/10 IDs; scan/DB about55 days old. Diagnostic and admission scopes remain held, strict rejection and riskDecision=null. Dedicated VM stopped.',
        target_evidence=f'Refresh only the four existing Flux references in security/resident-images.json, using installed trivy/syft/crane/cosign read-only registry operations. Private owned0600 outputs in {target}: scanner-version.json (trivy --version --format json after DB refresh), <component>.trivy.json (full fresh image JSON). Retain SBOM/publisher observations separately. Preserve failures if bytes/DB inaccessible; do not guess a new digest.',
        allowed_scope='Existing installed scanner tools, public read-only image/DB/advisory retrieval and local scanning only. Isolate ALL caches/temp/output under this mission private directory; no global installs/settings or shared cache writes. Max8GiB newly generated data, leave16GiB free. Exact linux/arm64 four Flux digest inputs only; no tags, replacement pins, executing image code, VM/container starts, registry writes or credential inspection. Repository changes only to focused refresh tooling/tests and evidence notes.',
        stop_conditions='No risk approvals/exceptions/riskDecision edits, no admission/policy activation, source transport selection, fetch/apply/unsuspend or Kubernetes/runtime changes. No retained evidence overwrite/deletion, spending/publication/API-key fallback. Unknown operations or permission denial hold dependent work; do not retry unknown requests. Do not call continuation runner. Record actual DB and scan times, never label stale evidence current.',
        recovery='VM stays stopped throughout. Preserve original ledger/manifests/security evidence unchanged, old holds/results/counters intact. Keep new private scan bytes even on failure; no automatic cache cleanup or data deletion. Commit a coherent scoped observation/tool increment, report remaining exact dependencies, checkpoint honestly; this evidence-only batch does not authorize runtime successors or L0 acceptance.')


def run(root, vault):
    parent = root/'tmp/local-experiment/flux-refresh'
    parent.mkdir(mode=0o700, exist_ok=True)
    st = parent.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
              stat.S_IMODE(st.st_mode) == 0o700, 'REFRESH_SCOPE_INVALID')
    fd = os.open(root/'tmp/local-experiment/gate-supervisor/lock', os.O_RDWR|os.O_NOFOLLOW)
    directory = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        R.require(len(list(parent.glob('refresh-*'))) < 2, 'REFRESH_APPROACH_EXHAUSTED')
        head = R.checkout_head(root, vault)
        S.no_active_worker(root)
        R.subscription_preflight('opencode', S.MODEL, root)
        S.check_resume_budget(root, 1, 120)
        R.require(shutil.disk_usage(root).free >= 24*1024**3, 'REFRESH_DISK_BUDGET_INVALID')
        profiles = stopped(root)
        images, digest = inputs(root)
        directory = Path(tempfile.mkdtemp(prefix='refresh-', dir=parent))
        R.save_json(directory/'before.json', {'profiles': profiles, 'images': images,
                                             'ledger_sha256': digest, 'head': head})
        for name in PRIOR:
            worker = root/'tmp/local-experiment/autonomous-runner'/name
            summary, result, sid = S.terminal(root, worker)
            R.require(summary.get('reason') == 'agent_no_safe_work_claim' and result['reason'] == 'no_safe_work' and
                      R.git(root, 'merge-base', '--is-ancestor', result['head_after'], head) == '',
                      'REFRESH_PRIOR_UNVERIFIED')
            receipt = dict(schema_version=1, source_run=name, original_summary=summary, original_result=result,
                checkpoint=R.private_json(worker/'attempt-001/checkpoint.json', 'CHECKPOINT_INVALID'),
                reviewed_head=head, last_session_id=sid, server_outcome='succeeded_idle_nonactive',
                allowed_gate=GATE, ledger_sha256=digest,
                decision='held_scope_exact_digest_evidence_refresh_no_replay')
            path = root/'tmp/local-experiment/gate-batches'/f'refresh-review-{name}.json'
            if path.exists():
                R.require(R.reviewed_refresh_stop(root, worker, summary, head, GATE), 'REFRESH_REVIEW_INVALID')
            else:
                R.save_json(path, receipt)
        R.gate_preflight(root, head, 1, 120, independent_gate=GATE)
        objective = mission(head, directory)
        R.require(all(30 <= len(objective[k]) <= 650 for k in
                      ('baseline','target_evidence','allowed_scope','stop_conditions','recovery')), 'MISSION_INVALID')
        R.save_json(directory/'mission.json', objective)
        started = time.time()
        # No retry/replay on a worker failure; a future parent may reconcile read-only.
        reason, worker = R.run(root, vault, 'opencode', max_runs=1, max_minutes=120,
                               model=S.MODEL, mission=objective)
        S.terminal(root, worker)
        after = R.checkout_head(root, vault)
        changed = R.git(root, 'diff', '--name-only', head, after).splitlines()
        R.require(all(p.startswith(('docs/design/', 'tools/local_experiment/flux_refresh',
                                    'tests/test_flux_refresh')) or
                      p in ('tools/local_experiment/FLUX_FINDINGS.md',) for p in changed),
                  'REFRESH_REPOSITORY_SCOPE_DRIFT')
        R.require(R.git(root, 'merge-base', '--is-ancestor', head, after) == '' and
                  not R.git(root, 'diff', '--name-only', head, after, '--', 'security', 'infra', 'clusters'),
                  'REFRESH_PROTECTED_INPUT_CHANGED')
        R.require(inputs(root)[1] == digest and stopped(root) == profiles,
                  'REFRESH_SCOPE_DRIFT')
        R.require(shutil.disk_usage(root).free >= 16*1024**3, 'REFRESH_DISK_BUDGET_INVALID')
        R.require(sum(p.lstat().st_size for p in directory.rglob('*') if p.is_file()) <= 8*1024**3,
                  'REFRESH_DISK_BUDGET_INVALID')
        focused = S.command(['python3', '-m', 'unittest', 'discover', '-s', 'tests',
                             '-p', 'test_flux_refresh*.py', '-q'], root, timeout=120)
        R.private_file(directory/'parent-focused.log', focused)
        try:
            observations = verify_scans(directory, images, started)
            classification = 'FRESH_SCAN_EVIDENCE_ONLY'
        except (R.RunnerError, OSError, ValueError, TypeError, AttributeError) as error:
            observations = {'evidence_refresh_verified': False,
                'reason': str(error) if isinstance(error,R.RunnerError) else 'REFRESH_RESPONSE_UNVERIFIED'}
            classification = 'REFRESH_DEPENDENCY_HELD'
        R.save_json(directory/'summary.json', {'classification': classification, 'worker': worker.name,
            'runner_reason': reason, 'head': after, 'observations': observations,
            'runtime_unchanged': True, 'gate_acceptance': False, 'automatic_runtime_successor': False})
        print(json.dumps({'evidence':str(directory), 'classification':classification}), flush=True)
        return directory
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json', {'classification':'HELD',
                'reason':str(error) if isinstance(error,R.RunnerError) else 'REFRESH_UNVERIFIED_STATE'})
        raise
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--use-pro-weekly-limit',action='store_true')
    args=p.parse_args()
    if not args.execute:
        print(json.dumps({'mode':'plan-only','active_calls':0,'max_sessions':1,'max_minutes':120}))
        return 0
    try:
        R.require(args.use_pro_weekly_limit,'BUDGET_REQUIRED')
        run(R.ROOT,R.VAULT)
        return 0
    except (R.RunnerError,OSError,ValueError,KeyError) as error:
        print(str(error) if isinstance(error,R.RunnerError) else 'REFRESH_UNVERIFIED_STATE')
        return 1


if __name__=='__main__': raise SystemExit(main())
