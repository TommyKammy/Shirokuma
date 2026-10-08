"""Finite L0 supervisor: repair owner chain, independently probe, measure readiness.

No daemon, arbitrary task planner, permission override or unknown-operation retry.
Default is a zero-call plan. Private append-only evidence and runner accounting
bound every worker. The parent probes Kubernetes itself, not worker assertions.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import stat
import signal

from tools.local_experiment import autonomous_runner as R

MODEL = 'openai/gpt-6.1-sol'
REPAIR = 'L0_OWNER_CHAIN_REPAIR'
READY = 'L0_CONTROLLER_READINESS'
PROFILE = 'mac-studio-solo'
FLUX = {'source-controller', 'kustomize-controller', 'helm-controller', 'notification-controller'}
APP_NS = {'shirokuma-dev', 'shirokuma-storage'}
SCOPED_NS = APP_NS | {'flux-system'}
RESOURCES = ('nodes,deployments,statefulsets,replicasets,replicationcontrollers,'
             'daemonsets,hpa,jobs,cronjobs,pods,pv,pvc,'
             'kustomizations.kustomize.toolkit.fluxcd.io,gitrepositories.source.toolkit.fluxcd.io')


def command(args, root, timeout=45):
    try:
        result = subprocess.run(args, cwd=root, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise R.RunnerError('SUPERVISOR_OPERATION_UNCONFIRMED') from None
    R.require(result.returncode == 0 and len(result.stdout) <= 16*1024*1024,
              'SUPERVISOR_PROBE_FAILED')
    return result.stdout


def api(root, path):
    return json.loads(command(['opencode', 'api', 'get', path], root))


def baseline_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        R.require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and
                  stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
                  0 < before.st_size <= 16*1024*1024, 'BASELINE_UNAVAILABLE')
        raw = os.read(fd, before.st_size+1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        R.require(identity(before) == identity(after) == identity(path.stat()) and len(raw) == before.st_size,
                  'BASELINE_CHANGED')
        return json.loads(raw, object_pairs_hook=R.unique_pairs)
    finally:
        os.close(fd)


def terminal(root, prior):
    summary = R.private_json(prior/'summary.json', 'PRIOR_GATE_RUN_UNVERIFIED')
    R.require(summary['attempts'] == 1, 'SUPERVISOR_SINGLE_WORKER_REQUIRED')
    attempt = prior/'attempt-001'
    ids = set()
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ('sessionID', 'sessionId', 'session_id') and isinstance(v, str):
                    ids.add(v)
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    for line in (attempt/'stdout.jsonl').read_text().splitlines():
        try:
            walk(json.loads(line))
        except ValueError:
            pass
    R.require(len(ids) == 1, 'SESSION_ID_UNVERIFIED')
    sid = ids.pop()
    session = api(root, '/api/session/'+sid)['data']
    active = api(root, '/api/session/active')
    R.require(session['outcome'] == 'succeeded' and session['time'].get('idle') and
              sid not in json.dumps(active) and
              session['model']['providerID'] == 'openai' and
              session['model']['id'] == 'gpt-6.1-sol' and
              session['location']['directory'] == str(root), 'SESSION_NOT_TERMINAL')
    result = R.private_json(attempt/'result.json', 'PRIOR_GATE_RUN_UNVERIFIED')
    checkpoint = R.private_json(attempt/'checkpoint.json', 'PRIOR_GATE_RUN_UNVERIFIED')
    R.require(R.read_checkpoint(attempt/'checkpoint.json',
              {'nonce': checkpoint['nonce'], 'iteration': 1}, result['head_after']) == result['reason'],
              'CHECKPOINT_INVALID')
    return summary, result, sid


def attestation(root, prior, head, diagnostic=False):
    summary, result, sid = terminal(root, prior)
    R.require(summary['reason'] in ('agent_safety_boundary_claim', 'agent_complete_claim'),
              'STOP_CLASSIFICATION_UNSUPPORTED')
    if diagnostic:
        R.require(result['reason'] == 'safety_boundary', 'REPAIR_NOT_SAFETY_STOP')
    return dict(schema_version=1, source_run=prior.name, original_summary=summary,
                original_result=result, resolution_head=head, last_session_id=sid,
                server_outcome='succeeded_idle_nonactive',
                decision=('terminal_safety_stop_repair_only_no_replay' if diagnostic else
                          'verified_stage_new_mission_no_replay' if result['reason'] == 'complete' else
                          'blocker_resolved_new_mission_no_replay'),
                **({'allowed_gate': REPAIR} if diagnostic else {}))


def no_active_worker(root):
    active = api(root, '/api/session/active')['data']
    R.require(isinstance(active, dict), 'ACTIVE_SESSIONS_UNVERIFIED')
    for sid in active:
        session = api(root, '/api/session/'+sid)['data']
        R.require(session['location']['directory'] != str(root), 'WORKTREE_SESSION_ACTIVE')


def validate(items, baseline, stage):
    """Fail closed on owner drift, retained identity drift or incomplete inventory."""
    R.require(stage in (REPAIR, READY), 'STAGE_INVALID')
    original = {i['metadata']['uid']: i for i in baseline}
    current = {i['metadata']['uid']: i for i in items}
    for uid, old in original.items():
        if old['kind'] in ('Deployment', 'StatefulSet', 'Job', 'PersistentVolume',
                           'PersistentVolumeClaim', 'Kustomization', 'GitRepository', 'Node'):
            R.require(uid in current and current[uid]['kind'] == old['kind'] and
                      current[uid]['metadata']['name'] == old['metadata']['name'], 'RESOURCE_IDENTITY_DRIFT')
    nodes = [i for i in items if i['kind'] == 'Node']
    R.require(len(nodes) == 1 and nodes[0]['metadata']['name'] == 'colima-mac-studio-solo', 'NODE_SCOPE_DRIFT')
    flux = set()
    ready_pods = set()
    secondary_replicas = {}
    for obj in items:
        kind = obj['kind']; meta = obj['metadata']; ns = meta.get('namespace')
        spec = obj.get('spec', {}); status = obj.get('status', {})
        R.require(ns is None or ns in SCOPED_NS | {'kube-system'}, 'UNEXPECTED_NAMESPACE')
        if kind in ('PersistentVolume', 'PersistentVolumeClaim'):
            R.require(meta['uid'] in original and spec == original[meta['uid']]['spec'], 'RETAINED_STATE_DRIFT')
        if kind in ('Kustomization', 'GitRepository', 'Job'):
            R.require(meta['uid'] in original and spec.get('suspend') is True and
                      not status.get('active'), 'RECONCILER_OR_JOB_NOT_INHIBITED')
        if kind in ('DaemonSet', 'CronJob', 'HorizontalPodAutoscaler', 'ReplicationController'):
            raise R.RunnerError('UNREVIEWED_OWNER_SURFACE')
        if kind in ('Deployment', 'StatefulSet'):
            R.require(meta['uid'] in original, 'UNREVIEWED_WORKLOAD')
            if ns in SCOPED_NS:
                expected = 1 if stage == READY and ns == 'flux-system' else 0
                R.require(spec.get('replicas') == expected, 'WRITER_NOT_INHIBITED')
                if ns == 'flux-system':
                    R.require(meta['name'] in FLUX and kind == 'Deployment', 'FLUX_SCOPE_DRIFT')
                    images = [c['image'] for c in spec['template']['spec']['containers']]
                    old_images = [c['image'] for c in original[meta['uid']]['spec']['template']['spec']['containers']]
                    R.require(images == old_images and all('@sha256:' in x for x in images), 'FLUX_IMAGE_DRIFT')
                    flux.add(meta['name'])
                    if stage == READY:
                        R.require(status.get('observedGeneration') == meta['generation'] and
                                  status.get('availableReplicas') == 1 and status.get('readyReplicas') == 1,
                                  'FLUX_NOT_READY')
        if kind == 'ReplicaSet' and ns in SCOPED_NS:
            owners = meta.get('ownerReferences', [])
            R.require(len(owners) == 1 and owners[0].get('controller') is True and
                      owners[0]['uid'] in current and current[owners[0]['uid']]['kind'] == 'Deployment',
                      'SECONDARY_OWNER_UNKNOWN')
            replicas = spec.get('replicas', 1)
            if stage == READY and ns == 'flux-system':
                R.require(type(replicas) is int and replicas in (0, 1), 'SECONDARY_OWNER_NOT_INHIBITED')
                name = current[owners[0]['uid']]['metadata']['name']
                secondary_replicas[name] = secondary_replicas.get(name, 0)+replicas
            else:
                R.require(replicas == 0, 'SECONDARY_OWNER_NOT_INHIBITED')
        if kind == 'Pod' and ns in SCOPED_NS:
            R.require(stage == READY and ns == 'flux-system', 'UNEXPECTED_APPLICATION_POD')
            refs = meta.get('ownerReferences', [])
            R.require(len(refs) == 1 and refs[0]['uid'] in current and
                      current[refs[0]['uid']]['kind'] == 'ReplicaSet', 'POD_OWNER_UNKNOWN')
            parent = current[refs[0]['uid']]
            owner = current[parent['metadata']['ownerReferences'][0]['uid']]
            R.require(owner['metadata']['name'] in FLUX, 'POD_OWNER_UNKNOWN')
            R.require(any(c['type'] == 'Ready' and c['status'] == 'True' for c in
                          status.get('conditions', [])) and not meta.get('deletionTimestamp'),
                      'FLUX_POD_NOT_READY')
            R.require([c['image'] for c in spec['containers']] ==
                      [c['image'] for c in owner['spec']['template']['spec']['containers']],
                      'FLUX_POD_IMAGE_DRIFT')
            ready_pods.add(owner['metadata']['name'])
    R.require(flux == FLUX, 'FLUX_SET_INCOMPLETE')
    R.require(sum(i['kind'] in ('Kustomization', 'GitRepository') for i in items) == 8 and
              sum(i['kind'] == 'Job' for i in items) == 3, 'INVENTORY_INCOMPLETE')
    if stage == READY:
        R.require(ready_pods == FLUX and secondary_replicas == {name: 1 for name in FLUX},
                  'FLUX_LIVE_PODS_INCOMPLETE')
        ready = [c for c in nodes[0]['status']['conditions'] if c['type'] == 'Ready']
        R.require(len(ready) == 1 and ready[0]['status'] == 'True' and
                  abs((datetime.now(timezone.utc)-datetime.fromisoformat(
                      ready[0]['lastHeartbeatTime'].replace('Z', '+00:00'))).total_seconds()) < 180,
                  'NODE_NOT_FRESH_READY')


def probe(root, stage, directory, number):
    prefix = ['colima', 'ssh', '--profile', PROFILE, '--']
    if stage == REPAIR:
        state = command(prefix+['systemctl', 'show', 'k3s', '--property=ActiveState', '--value'], root)
        R.require(state.strip() == b'inactive',
                  'NORMAL_K3S_ACTIVE')
        processes = command(prefix+['ps', '-eo', 'args'], root).decode().splitlines()
        servers = [line for line in processes if '/usr/local/bin/k3s server' in line]
        R.require(len(servers) == 1 and all(flag in servers[0].split() for flag in
                  ('--disable-agent', '--disable-scheduler', '--disable-controller-manager',
                   '--disable-cloud-controller')), 'API_ONLY_FLAGS_UNVERIFIED')
    raw = command(prefix+['sudo', 'k3s', 'kubectl', '--request-timeout=20s',
                         'get', RESOURCES, '-A', '-o', 'json'], root)
    value = json.loads(raw)
    R.require(value.get('kind') == 'List' and not value.get('metadata', {}).get('continue'),
              'INVENTORY_INCOMPLETE')
    R.save_json(directory/f'probe-{number:02d}.json', value)
    baseline = baseline_file(root/'tmp/local-experiment/lab-inspection-20261007/api-prior-resources.json')['items']
    validate(value['items'], baseline, stage)
    running = command(prefix+['docker', 'ps', '--format', '{{.Names}}'], root).decode().splitlines()
    if stage == REPAIR:
        R.require(not running, 'CONTAINER_RUNNING_DURING_REPAIR')
    else:
        R.require(all(n.startswith('k8s_') and ('_kube-system_' in n or '_flux-system_' in n)
                      for n in running), 'UNEXPECTED_RUNNING_CONTAINER')
    return {'stage': stage, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'objects': len(value['items']), 'running_containers': len(running), 'verified': True}


def mission(head, stage):
    repair = stage == REPAIR
    return dict(schema_version=1, wp_id='WP-L0-GITOPS-001', gate_id=stage, head_before=head,
        baseline='DEV-04D/04E: gate-uklhzopt found six nonzero ReplicaSets behind zero-replica Deployments. Preserve original safety stops and cold-backup. Parent supervision requires independently probed owner inhibition before node start.',
        target_evidence=('Capture complete Deployment/ReplicaSet/Pod owner chain and UID/version guarded inhibition in API-only stage. Recheck PV/PVC, Flux8/Jobs3 suspend and all app/Flux parents/secondary owners zero. Leave API-only available for parent live verification, recording its unit name.' if repair else
                         'Recheck complete inhibition first, then obtain fresh dedicated Node Ready and four digest-pinned Flux controller available/ready observations. Sources/Kustomizations/Jobs remain suspended and applications zero. Capture scoped doctor and stale source status honestly. Leave runtime for parent live verification.'),
        allowed_scope=('mac-studio-solo ONLY. Follow DEV-04E API-only setup with agent/scheduler/controller-manager/cloud-controller disabled. Capture originals then inhibit matching ReplicaSets with UID/version guards. No normal k3s/node/Flux/workload startup.' if repair else
                       'mac-studio-solo ONLY. Recheck API-only owner-chain inhibition. Stop API-only cleanly before starting dedicated normal k3s. Keep all retained app owners zero, all source/root/child Flux and Jobs suspended. Enable only four existing digest-pinned Flux Deployments; preserve storage and backup identities.'),
        stop_conditions='Stop dependent mutations on unknown operations/owners, denied permissions, retention drift or missing recovery. No request replay, spending, account/provider fallback, GitHub writes, public exposure, global settings or retained-data deletion. Do not recursively launch the supervisor/runner. Preserve unexpected states.',
        recovery='Parent will independently probe terminal worker and runtime, then normally stop dedicated VM at finite-loop end. Preserve original and changed metadata/disk evidence. Do not restore writer replicas or unsuspend sources/Jobs. Keep cold-backup, PV/PVC/volumes and all failed results. Record mutations and exact rollback.')


def supervise(root, vault, max_sessions, minutes):
    R.require(1 <= max_sessions <= 3 and 1 <= minutes <= 120, 'BUDGET_INVALID')
    parent = root/'tmp/local-experiment/gate-supervisor'
    parent.mkdir(mode=0o700, exist_ok=True)
    state = parent.lstat()
    R.require(stat.S_ISDIR(state.st_mode) and state.st_uid == os.getuid() and
              stat.S_IMODE(state.st_mode) == 0o700, 'SUPERVISOR_SCOPE_INVALID')
    fd = os.open(parent/'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    confirmed_idle = False
    directory = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A second invocation cannot reset the stable repair counter.
        prior_claims = sum(1 for d in parent.glob('loop-*') for p in d.glob('repair-start-*.json'))
        R.require(prior_claims < 2, 'REPAIR_APPROACH_EXHAUSTED')
        directory = Path(tempfile.mkdtemp(prefix='loop-', dir=parent))
        deadline = time.monotonic()+minutes*60
        stage = REPAIR
        runs = root/'tmp/local-experiment/autonomous-runner'
        gate = root/'tmp/local-experiment/gate-batches'
        # Reconcile every unresolved stop, including earlier preserved boundaries.
        for prior in sorted(runs.glob('gate-*')):
            s = R.private_json(prior/'summary.json', 'PRIOR_GATE_RUN_UNVERIFIED')
            if s['reason'] != 'run_budget_reached' and not (gate/f'review-{prior.name}.json').exists():
                review = attestation(root, prior, R.checkout_head(root, vault), diagnostic=True)
                path = gate/f'repair-review-{prior.name}.json'
                if path.exists():
                    R.require(R.private_json(path, 'REVIEW_INVALID') == review, 'STALE_REPAIR_REVIEW')
                else:
                    R.save_json(path, review)
        for number in range(1, max_sessions+1):
            remaining = int((deadline-time.monotonic())//60)
            R.require(remaining >= 1, 'SUPERVISOR_TIME_EXHAUSTED')
            head = R.checkout_head(root, vault)
            no_active_worker(root)
            R.subscription_preflight('opencode', MODEL, root)
            objective = mission(head, stage)
            if stage == REPAIR:
                R.require(prior_claims < 2, 'REPAIR_APPROACH_EXHAUSTED')
                R.save_json(directory/f'repair-start-{number:02d}.json',
                            {'blocker': 'L0-secondary-owner-replicas', 'approach': 'API-only-UID-inhibition', 'head': head})
                prior_claims += 1
            R.save_json(directory/f'mission-{number:02d}.json', objective)
            confirmed_idle = False
            reason, run = R.run(root, vault, 'opencode', max_runs=1, max_minutes=remaining,
                                model=MODEL, mission=objective)
            terminal(root, run)  # Unknown/failed workers never enter mutation/retry transitions.
            confirmed_idle = True
            R.require(reason in ('agent_complete_claim', 'run_budget_reached'),
                      'STOP_CLASSIFICATION_UNSUPPORTED')
            try:
                observed = probe(root, stage, directory, number)
            except R.RunnerError as error:
                R.save_json(directory/f'review-{number:02d}.json', {'stage': stage, 'classification': 'HELD', 'reason': str(error)})
                # No blind replay when the live probe cannot establish disposition.
                raise
            R.save_json(directory/f'review-{number:02d}.json', observed)
            head = R.checkout_head(root, vault)
            for prior in sorted(runs.glob('gate-*')):
                if not (gate/f'review-{prior.name}.json').exists():
                    summary = R.private_json(prior/'summary.json', 'PRIOR_GATE_RUN_UNVERIFIED')
                    if summary['reason'] != 'run_budget_reached':
                        R.save_json(gate/f'review-{prior.name}.json', attestation(root, prior, head))
            if stage == READY:
                R.save_json(directory/'summary.json', {'classification': 'L0_SCOPED_READY', 'sessions': number,
                            'full_acceptance': False, 'next': 'review source/admission gate; no automatic unsuspension'})
                return directory
            stage = READY
        raise R.RunnerError('SUPERVISOR_SESSION_BOUND_REACHED')
    except (Exception, KeyboardInterrupt) as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json', {'classification': 'HELD',
                        'reason': str(error) if isinstance(error, R.RunnerError) else 'UNVERIFIED_STATE',
                        'runtime_cleanup_permitted': confirmed_idle})
        raise
    finally:
        # Never mutate runtime if server execution could still be active/unknown.
        if confirmed_idle and directory is not None:
            try:
                command(['colima', 'stop', PROFILE], root, timeout=180)
                R.save_json(directory/'cleanup.json', {'dedicated_vm_stopped': True,
                            'controllers_restored': False, 'retained_data_deleted': False})
            except R.RunnerError:
                R.save_json(directory/'cleanup.json', {'dedicated_vm_stopped': 'unconfirmed',
                            'retry': False})
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--use-pro-weekly-limit', action='store_true')
    p.add_argument('--max-sessions', type=int, default=2)
    p.add_argument('--max-minutes', type=int, default=120)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps({'mode': 'plan-only', 'active_calls': 0, 'stages': [REPAIR, READY],
                          'max_sessions': args.max_sessions, 'max_minutes': args.max_minutes}))
        return 0
    try:
        R.require(args.use_pro_weekly_limit, 'BUDGET_REQUIRED')
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            directory = supervise(R.ROOT, R.VAULT, args.max_sessions, args.max_minutes)
        finally:
            signal.signal(signal.SIGTERM, previous)
        print(json.dumps({'evidence': str(directory), 'full_acceptance': False}))
        return 0
    except (R.RunnerError, OSError, ValueError, KeyError) as error:
        print(str(error) if isinstance(error, R.RunnerError) else 'SUPERVISOR_UNVERIFIED_STATE')
        return 1
    except KeyboardInterrupt:
        print('SUPERVISOR_INTERRUPTED_REMOTE_UNCONFIRMED')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
