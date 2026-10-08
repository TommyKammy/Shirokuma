"""Bounded source-controller rebuild evidence, never an admission or deploy job."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime

from tools.local_experiment import flux_pin_candidate as P

R, S, F = P.R, P.S, P.F
GATE = 'L0_FLUX_LOCAL_BUILD'
PRIOR = ('gate-azr7paf4', 'gate-5d3ur67c', 'gate-3f5odnk2')


def objective(head, directory):
    path=str(directory.relative_to(R.ROOT))
    return dict(schema_version=1,wp_id='WP-L0-GITOPS-001',gate_id=GATE,head_before=head,
        baseline='Parent verified v2.9.6 candidate scans HIGH28/CRITICAL0; source-controller v1.9.6 has HIGH9. No newer stable same-series tags observed for any controller. Runtime/pins/ledger unchanged, all profiles Stopped; old scopes held and admission false.',
        target_evidence=f'Produce ONE isolated source-controller v1.9.6 patched Linux/ARM64 Go binary and reproducible source/build/test records under {path}. Name the owned0600 binary source-controller. Current findings require go-git5.19.2, Fulcio1.8.6, x/crypto0.55.0, x/mod0.40.0, grpc1.83.2, oras-go2.6.2 or independently proven fixed stable successors. Run relevant upstream tests and inspect Go build metadata/SBOM/fresh binary scan. Image/base/publisher acceptance remain separate.',
        allowed_scope='Public read-only pinned source/module/toolchain retrieval; compile/test one controller using isolated HOME/GOCACHE/GOMODCACHE/GOPATH/TMPDIR under the mission directory. Exact upstream tag resolved once to full commit/tree, recorded before patching. Installed host build tools only; temporary Go toolchain download allowed, no global installs/settings. Max8GiB new data/free16GiB. Repository changes only tools/local_experiment/flux_local_build*, matching tests and evidence notes. No image execution, VM/container starts or external writes.',
        stop_conditions='No OCI image assembly/execution. Do not change existing Flux pins/security ledger/retained evidence or activate policy/admission. No approvals/exceptions/riskDecision edits or source/apply/unsuspend/runtime changes. Unknown operation or denied access holds dependent action, never replay to fill budget. No nested runner or API-key/provider fallback. Keep upstream failures and build provenance; a compiled binary is not image readiness or full L0 acceptance.',
        recovery='All profiles stay Stopped, retained disks/PV/PVC/backups unchanged. Preserve source snapshots, patches, caches, logs and failed evidence; no auto-cleanup. Record exact base/image/publisher and remaining other-controller dependencies without inventing pins. Commit one coherent scoped build observation/tool increment; parent independently inspects binary metadata and current scope. Rollback repository changes only, never retained data.')


def verify_binary(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd);header=os.read(fd,64)
        R.require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and
                  stat.S_IMODE(st.st_mode)==0o600 and st.st_nlink==1 and
                  header[:6]==b'\x7fELF\x02\x01' and
                  int.from_bytes(header[18:20],'little')==183,'BUILD_BINARY_IDENTITY_UNVERIFIED')
    finally: os.close(fd)
    return {'elf':'AArch64 ELF64 little endian; OS requires build metadata',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'executed':False,'image_acceptance':False}


def run(root,vault):
    parent=root/'tmp/local-experiment/flux-local-build';parent.mkdir(mode=0o700,exist_ok=True)
    st=parent.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and
              stat.S_IMODE(st.st_mode)==0o700,'BUILD_SCOPE_INVALID')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    directory=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        R.require(len(list(parent.glob('build-*')))<2,'BUILD_APPROACH_EXHAUSTED')
        head=R.checkout_head(root,vault);S.no_active_worker(root)
        R.subscription_preflight('opencode',S.MODEL,root);S.check_resume_budget(root,1,120)
        profiles=F.stopped(root);_,ledger_hash=F.inputs(root)
        prior=root/'tmp/local-experiment/flux-pin-candidate/candidate-hn94027w'
        candidate=R.private_json(prior/'candidate-inputs.json','BUILD_CANDIDATE_UNVERIFIED')
        intent=P.Scan.F.S.baseline_file(prior/'intent.json')
        scans=F.verify_scans(prior,candidate['images'],datetime.fromisoformat(intent['started_at']).timestamp())
        tags=R.private_json(prior/'stable-tag-observations.json','BUILD_CANDIDATE_UNVERIFIED')
        R.require(len(tags.get('observations',[]))==4 and
                  all(not x['newer_same_series'] for x in tags['observations']), 'BUILD_NEW_APPROACH_UNVERIFIED')
        candidate_hash=hashlib.sha256((prior/'summary.json').read_bytes()).hexdigest()
        directory=Path(tempfile.mkdtemp(prefix='build-',dir=parent))
        P.Scan.check_budget(directory)
        R.save_json(directory/'parent-before.json',dict(scans=scans,profiles=profiles,
            candidate_summary_sha256=candidate_hash,ledger_sha256=ledger_hash,head=head))
        for name in PRIOR:
            worker=root/'tmp/local-experiment/autonomous-runner'/name
            summary,result,sid=S.terminal(root,worker)
            R.require(summary.get('reason')=='agent_no_safe_work_claim' and result['reason']=='no_safe_work' and
                      R.git(root,'merge-base','--is-ancestor',result['head_after'],head)=='','BUILD_PRIOR_UNVERIFIED')
            R.save_json(root/'tmp/local-experiment/gate-batches'/f'build-review-{name}.json',dict(
                schema_version=1,source_run=name,original_summary=summary,original_result=result,
                checkpoint=R.private_json(worker/'attempt-001/checkpoint.json','CHECKPOINT_INVALID'),
                reviewed_head=head,last_session_id=sid,server_outcome='succeeded_idle_nonactive',
                allowed_gate=GATE,ledger_sha256=ledger_hash,candidate_summary_sha256=candidate_hash,
                decision='held_scope_isolated_source_build_no_replay'))
        R.gate_preflight(root,head,1,120,independent_gate=GATE)
        mission=objective(head,directory)
        R.require(all(30<=len(mission[k])<=650 for k in
                      ('baseline','target_evidence','allowed_scope','stop_conditions','recovery')),'MISSION_INVALID')
        R.save_json(directory/'mission.json',mission)
        reason,worker=R.run(root,vault,'opencode',max_runs=1,max_minutes=120,model=S.MODEL,mission=mission)
        S.terminal(root,worker)
        after=R.checkout_head(root,vault)
        changes=R.git(root,'diff','--name-only',head,after).splitlines()
        R.require(all(p.startswith(('docs/design/','tools/local_experiment/flux_local_build',
                                    'tests/test_flux_local_build')) for p in changes) and
                  F.inputs(root)[1]==ledger_hash and F.stopped(root)==profiles,'BUILD_SCOPE_DRIFT')
        P.Scan.check_budget(directory)
        try:
            binary=verify_binary(directory/'source-controller')
            metadata=S.command(['go','version','-m',str(directory/'source-controller')],root,timeout=30)
            R.private_file(directory/'parent-go-build-metadata.log',metadata)
            R.require(b'GOOS=linux' in metadata and b'GOARCH=arm64' in metadata and
                      b'github.com/fluxcd/source-controller' in metadata,'BUILD_GO_METADATA_UNVERIFIED')
            classification='BINARY_METADATA_EVIDENCE_ONLY'
        except (R.RunnerError,OSError) as error:
            binary={'verified':False,'reason':str(error) if isinstance(error,R.RunnerError) else 'BINARY_MISSING'}
            classification='LOCAL_BUILD_DEPENDENCY_HELD'
        R.save_json(directory/'summary.json',dict(classification=classification,worker=worker.name,
            runner_reason=reason,binary=binary,admission=False,gate_acceptance=False,runtime_unchanged=True))
        print(json.dumps({'evidence':str(directory),'classification':classification}))
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json',dict(classification='HELD',operation_replay=False,
                reason=str(error) if isinstance(error,R.RunnerError) else type(error).__name__))
        raise
    finally: fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--use-pro-weekly-limit',action='store_true')
    args=p.parse_args()
    if not args.execute:
        print(json.dumps({'mode':'plan-only','active_calls':0,'max_sessions':1,'max_minutes':120}));return 0
    try:
        R.require(args.use_pro_weekly_limit,'BUDGET_REQUIRED');run(R.ROOT,R.VAULT);return 0
    except (R.RunnerError,OSError,ValueError,KeyError) as error:
        print(str(error) if isinstance(error,R.RunnerError) else 'BUILD_UNVERIFIED_STATE');return 1


if __name__=='__main__': raise SystemExit(main())
