"""Reviewed repo-only capacity repair followed by one conditional API-test pilot."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
import time

from tools.local_experiment import flux_local_build_supervisor as B

R, S, F = B.R, B.S, B.F
GATE = 'L0_SUPERVISION_CAPACITY_REPAIR'
PRIOR = B.PRIOR+('gate-xezx_z6k','gate-ayqdekwa')


def retained(root):
    """Small fixed immutable bindings, not a self-attested worker flag."""
    files=['security/resident-images.json']
    for scope in ('build-aoa39q7w','build-don5ufj3'):
        prefix='tmp/local-experiment/flux-local-build/'+scope+'/'
        files += [prefix+p for p in ('summary.json','source-controller','build-observation.json',
                   'source/go.mod','source/go.sum','source-final-diff.stdout')]
    files += ['tmp/local-experiment/flux-local-build/build-don5ufj3/'+p for p in
              ('source-snapshot.stdout','go1.26.6.darwin-arm64.tar.gz','toolchain-checksum.json')]
    files += ['tmp/local-experiment/flux-local-image/image-qv4_7qqh/'+p for p in
              ('summary.json','parent-resolution.json','source-controller.tar')]
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files}


def mission(head, directory):
    plan=str((directory/'restart-plan.json').relative_to(R.ROOT))
    return dict(schema_version=1,wp_id='WP-L0-GITOPS-001',gate_id=GATE,head_before=head,
        baseline='Owner requested safe restart on2026-10-09. All profiles Stopped; previous binaries/full local source image have dated scan evidence, no admission. Two source binary repairs exhausted. Parent7GiB bulk guard wrongly also inhibits read-only validation below hard8GiB. Keep all original holds and bytes.',
        target_evidence=f'Implement tested tools/local_experiment/capacity.py contract observe(path): owned private directory, sum max(logical,allocated), hard cap8GiB/free16GiB and conservative bulk7GiB as separate booleans hard_limit_exceeded, bulk_writes_allowed, used_bytes. Read-only observation may report over-soft/below-hard without exception or relaxing guards. Update relevant parent callers. Write owned0600 {plan}: exact schema_version=1, decision=ready_for_api_pilot|hold, pilot_package=api, notes (30-350 chars).',
        allowed_scope='Repository-only capacity/supervision repair and regression tests. Allowed files tools/local_experiment/capacity.py, restart_supervisor.py, flux_local_build*.py, flux_refresh_scan.py and matching tests/design/runner notes. No source/controller builds, Go tests, downloads or writes to old private source/cache/binary/image/evidence. New small diagnostic records in this restart scope only. Define typed observation and preserve writer guards; parent independently reviews before a single separate API-test pilot.',
        stop_conditions='No counter reset, hard-cap increase, third source binary attempt, unknown-operation replay, cleanup/deletion, VM/container start, image execution, registry/publication writes, existing pin/ledger change, approval/exception/admission activation, global settings or extra spending. Default plan makes no model requests. Do not run any continuation runner. Treat old scan timestamps as historical, never renew freshness through prose.',
        recovery='Commit coherent repaired supervisor/capacity code and focused tests; preserve originals and all failure records. Keep profiles Stopped and old inputs read-only. Parent tests capacity contracts, hard/soft bounds, retention and terminal/session state, then selects one new isolated API-package verification approach only if ready. A successful capacity fix or API pilot is not full upstream tests, publisher/admission or L0 acceptance.')


def plan(value):
    R.require(isinstance(value,dict) and set(value)=={'schema_version','decision','pilot_package','notes'} and
              type(value['schema_version']) is int and value['schema_version']==1 and
              value['decision'] in ('ready_for_api_pilot','hold') and value['pilot_package']=='api' and
              isinstance(value['notes'],str) and 30<=len(value['notes'])<=350 and
              not any(ord(c)<32 for c in value['notes']),'RESTART_PLAN_INVALID')
    return value


def review_capacity(root):
    """Compare new contract with independent old accounting, without old writes."""
    C=importlib.import_module('tools.local_experiment.capacity')
    observed=[]
    for name in ('build-aoa39q7w','build-don5ufj3'):
        directory=root/'tmp/local-experiment/flux-local-build'/name
        value=C.observe(directory)
        R.require(isinstance(value,dict) and set(value)=={'used_bytes','free_bytes',
                  'hard_limit_exceeded','bulk_writes_allowed'} and
                  type(value['used_bytes']) is int and type(value['free_bytes']) is int and
                  value['used_bytes']>=0 and value['free_bytes']>=16*1024**3 and
                  value['used_bytes']==measure(directory) and
                  7*1024**3<=value['used_bytes']<8*1024**3 and
                  shutil.disk_usage(directory).free>=16*1024**3 and
                  value['hard_limit_exceeded'] is False and value['bulk_writes_allowed'] is False,
                  'CAPACITY_PARENT_REVIEW_FAILED')
        observed.append({'scope':name,'observation':value})
    return observed


def capacity_tests(root,directory):
    """Use the launch interpreter and preserve both outputs even on failure."""
    argv=[sys.executable,'-m','unittest','discover','-s','tests','-p','test_capacity*.py','-q']
    R.save_json(directory/'parent-capacity-tests.started.json',dict(argv=argv))
    result=subprocess.run(argv,cwd=root,capture_output=True,timeout=120)
    R.require(len(result.stdout)+len(result.stderr)<=R.MAX_LOG_BYTES,'OUTPUT_LIMIT_EXCEEDED')
    R.private_file(directory/'parent-capacity-tests.stdout',result.stdout)
    R.private_file(directory/'parent-capacity-tests.stderr',result.stderr)
    R.save_json(directory/'parent-capacity-tests.finished.json',dict(exit_code=result.returncode,
        interpreter=sys.executable,stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(result.stderr).hexdigest()))
    R.require(result.returncode==0 and re.search(rb'Ran [1-9][0-9]* tests?\b',result.stderr),
              'CAPACITY_PARENT_TESTS_FAILED')


def measure(directory):
    """Parent-side accounting stays loaded across the worker's code changes."""
    st=directory.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and
              stat.S_IMODE(st.st_mode)==0o700,'CAPACITY_INPUT_UNSAFE')
    total=0
    def fail(error):raise error
    for base,dirs,files in os.walk(directory,followlinks=False,onerror=fail):
        for name in dirs+files:
            st=(Path(base)/name).lstat()
            R.require(st.st_uid==os.getuid() and not stat.S_ISLNK(st.st_mode) and
                      (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)),'CAPACITY_INPUT_UNSAFE')
            if stat.S_ISREG(st.st_mode):total+=max(st.st_size,st.st_blocks*512)
    return total


def verified_pilot_inputs(old):
    observation=S.baseline_file(old/'build-observation.json')
    archive=old/'source-snapshot.stdout'
    R.require(hashlib.sha256(archive.read_bytes()).hexdigest()==observation['hashes']['source-snapshot.stdout'],
              'API_PILOT_SOURCE_UNVERIFIED')
    source=old/'source/api'; expected={}
    with tarfile.open(archive,'r:*') as tar:
        for member in tar.getmembers():
            if member.name.startswith('api/') and member.isfile():
                rel=Path(member.name).relative_to('api')
                R.require('..' not in rel.parts,'API_PILOT_SOURCE_UNVERIFIED')
                expected[str(rel)]=hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    actual={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
    R.require(actual==expected and actual,'API_PILOT_SOURCE_UNVERIFIED')
    tools=list(old.glob('**/go/bin/go'))
    R.require(len(tools)==1 and tools[0].is_file() and not tools[0].is_symlink(),'API_PILOT_TOOLCHAIN_UNVERIFIED')
    archive=old/'go1.26.6.darwin-arm64.tar.gz'
    R.require(hashlib.sha256(archive.read_bytes()).hexdigest()==observation['hashes'][archive.name],
              'API_PILOT_TOOLCHAIN_UNVERIFIED')
    with tarfile.open(archive,'r:gz') as tar:
        expected=hashlib.sha256(tar.extractfile('go/bin/go').read()).hexdigest()
    R.require(hashlib.sha256(tools[0].read_bytes()).hexdigest()==expected,'API_PILOT_TOOLCHAIN_UNVERIFIED')
    return source,tools[0]


def pilot_tests(evidence,tool,source,extra,timeout):
    """Fixed test argv, owned process group, terminal record even on guard failure."""
    C=importlib.import_module('tools.local_experiment.capacity')
    directory=evidence.directory
    env=evidence.environment();env.update(extra)
    argv=[str(tool),'test','-mod=readonly','-p=2','-count=1','-json','-timeout=15m','./...']
    R.require(C.observe(directory)['bulk_writes_allowed'] is True,'API_PILOT_WRITE_GUARD')
    R.save_json(directory/'api-unit-tests.started.json',dict(argv=argv,cwd=str(source),
        started_at=datetime.now(timezone.utc).isoformat(),process_group_owned=True))
    process=None;error=None
    try:
        with R.private_log(directory/'api-unit-tests.stdout') as out,R.private_log(directory/'api-unit-tests.stderr') as err:
            process=subprocess.Popen(argv,cwd=source,env=env,stdout=out,stderr=err,start_new_session=True)
            end=time.monotonic()+timeout
            while process.poll() is None:
                R.require(C.observe(directory)['bulk_writes_allowed'] is True,'API_PILOT_WRITE_GUARD')
                R.require(time.monotonic()<end,'API_PILOT_TIMEOUT')
                time.sleep(0.5)
        return process.returncode
    except Exception as caught:
        error=str(caught) if isinstance(caught,R.RunnerError) else type(caught).__name__
        raise
    finally:
        termination_error=None
        try:
            if process is not None and process.poll() is None:R.terminate(process)
        except R.RunnerError as caught:
            termination_error=str(caught)
            raise
        finally:
            R.save_json(directory/'api-unit-tests.finished.json',dict(
                returncode=None if process is None else process.returncode,observation_error=error,
                termination_error=termination_error,
                disposition='succeeded' if process is not None and process.returncode==0 and error is None and termination_error is None else 'held_no_replay',
                finished_at=datetime.now(timezone.utc).isoformat(),
                stdout_sha256=hashlib.sha256((directory/'api-unit-tests.stdout').read_bytes()).hexdigest()))


def api_pilot(root,directory,deadline):
    """One new narrow API-module verification, not third binary build or replay."""
    from tools.local_experiment import flux_local_build as Local
    S.no_active_worker(root);S.check_resume_budget(root,0,60)
    R.require(deadline-time.monotonic()>180,'RESTART_PARENT_TIME_BUDGET_EXPIRED')
    parent=root/'tmp/rv';parent.mkdir(mode=0o700,exist_ok=True)
    st=parent.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o700,
              'API_PILOT_SCOPE_INVALID')
    R.require(len(list(parent.glob('api-*')))<2,'API_PILOT_APPROACH_EXHAUSTED')
    pilot=Path(tempfile.mkdtemp(prefix='api-',dir=parent))
    R.require(len(os.fsencode(str(pilot/'temp')))<85,'API_PILOT_TEMP_PATH_TOO_LONG')
    old=root/'tmp/local-experiment/flux-local-build/build-don5ufj3'
    # Source copy only, no old cache/toolchain writes and no refetch of source tag.
    source,tool=verified_pilot_inputs(old)
    R.require(source.is_dir() and not source.is_symlink(),'API_PILOT_SOURCE_UNVERIFIED')
    for p in source.rglob('*'):
        R.require(not p.is_symlink() and (p.is_file() or p.is_dir()),'API_PILOT_SOURCE_UNVERIFIED')
    shutil.copytree(source,pilot/'source')
    evidence=Local.Evidence(pilot);evidence.environment(create=True)
    # Locate only the already verified toolchain, never arbitrary executables from a proposal.
    extra={'GOTOOLCHAIN':'local','GOMAXPROCS':'2','GOTELEMETRY':'off','GOWORK':'off'}
    R.save_json(pilot/'intent.json',dict(approach='new isolated API-module test, not source binary rebuild',
        package='api',toolchain='Go1.26.6 read-only retained toolchain',old_scope_writes=False,
        hard_cap_bytes=8*1024**3,deadline_minutes=60,replay=False))
    # The first command is a version read; all writable Go paths are new/private.
    evidence.command('toolchain-version',[str(tool),'version'],extra=extra,timeout=30)
    R.require(b'go1.26.6' in (pilot/'toolchain-version.stdout').read_bytes(),'API_PILOT_TOOLCHAIN_UNVERIFIED')
    code=pilot_tests(evidence,tool,pilot/'source',extra,
                     timeout=min(3000,int(deadline-time.monotonic()-120)))
    events=[json.loads(line) for line in (pilot/'api-unit-tests.stdout').read_text().splitlines()]
    tests=sum(v.get('Action')=='pass' and bool(v.get('Test')) for v in events)
    packages=sum(v.get('Action')=='pass' and not v.get('Test') for v in events)
    R.require(code!=0 or (tests>0 and packages>0),'API_PILOT_EMPTY_SUCCESS_UNVERIFIED')
    R.save_json(directory/'pilot-review.json',dict(evidence=str(pilot.relative_to(root)),exit_code=code,
        classification='API_PACKAGE_TESTS_PASSED' if code==0 else 'API_PACKAGE_TESTS_FAILED',
        test_and_subtest_passes=tests,package_passes=packages,
        full_upstream_tests_complete=False,admission=False,gate_acceptance=False))
    return code


def run(root,vault):
    parent=root/'tmp/local-experiment/safe-restart';parent.mkdir(mode=0o700,exist_ok=True)
    st=parent.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o700,
              'RESTART_SCOPE_INVALID')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    directory=None
    deadline=time.monotonic()+120*60
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        R.require(len(list(parent.glob('restart-*')))<2,'RESTART_REPAIR_APPROACH_EXHAUSTED')
        head=R.checkout_head(root,vault);S.no_active_worker(root)
        S.check_resume_budget(root,1,105);R.subscription_preflight('opencode',S.MODEL,root)
        profiles=F.stopped(root);before=retained(root)
        directory=Path(tempfile.mkdtemp(prefix='restart-',dir=parent))
        R.save_json(directory/'before.json',dict(head=head,retained=before,profiles=profiles,
                                               started_at=datetime.now(timezone.utc).isoformat()))
        for name in PRIOR:
            worker=root/'tmp/local-experiment/autonomous-runner'/name
            summary,result,sid=S.terminal(root,worker)
            R.require(result['reason']=='no_safe_work','RESTART_PRIOR_UNVERIFIED')
            receipt=dict(schema_version=1,source_run=name,original_summary=summary,original_result=result,
                checkpoint=R.private_json(worker/'attempt-001/checkpoint.json','CHECKPOINT_INVALID'),
                reviewed_head=head,last_session_id=sid,server_outcome='succeeded_idle_nonactive',
                allowed_gate=GATE,ledger_sha256=before['security/resident-images.json'],
                decision='old_scope_held_repository_capacity_repair_no_replay')
            path=root/'tmp/local-experiment/gate-batches'/f'restart-review-{name}.json'
            if path.exists():R.require(R.reviewed_restart_stop(root,worker,summary,head,GATE),'RESTART_PRIOR_UNVERIFIED')
            else:R.save_json(path,receipt)
        R.gate_preflight(root,head,1,45,independent_gate=GATE)
        objective=mission(head,directory)
        R.require(all(30<=len(objective[k])<=650 for k in ('baseline','target_evidence','allowed_scope','stop_conditions','recovery')),
                  'MISSION_INVALID')
        R.save_json(directory/'mission.json',objective)
        reason,worker=R.run(root,vault,'opencode',max_runs=1,max_minutes=45,model=S.MODEL,mission=objective)
        S.terminal(root,worker)
        R.require(reason in ('run_budget_reached','agent_complete_claim','agent_no_safe_work_claim'),
                  'RESTART_STOP_UNCLASSIFIED')
        after=R.checkout_head(root,vault)
        changes=R.git(root,'diff','--name-only',head,after).splitlines()
        R.require(after!=head and all(p.startswith(('docs/design/','tests/test_capacity','tests/test_flux_local_build',
            'tests/test_flux_refresh','tests/test_restart_supervisor','tools/local_experiment/flux_local_build')) or p in
            ('tools/local_experiment/capacity.py','tools/local_experiment/restart_supervisor.py',
             'tools/local_experiment/flux_refresh_scan.py','tools/local_experiment/AUTONOMOUS_RUNNER.md') for p in changes),
             'RESTART_SCOPE_DRIFT')
        R.require(retained(root)==before and F.stopped(root)==profiles,'RESTART_RETENTION_DRIFT')
        proposal=plan(R.private_json(directory/'restart-plan.json','RESTART_PLAN_INVALID'))
        capacity_tests(root,directory)
        observed=review_capacity(root)
        R.save_json(directory/'capacity-review.json',dict(head=after,worker=worker.name,proposal=proposal,
            observations=observed,old_scopes_held=True,counters_reset=False,gate_acceptance=False))
        if reason=='agent_complete_claim':
            R.save_json(root/'tmp/local-experiment/gate-batches'/f'review-{worker.name}.json',
                        S.attestation(root,worker,after))
        if proposal['decision']=='ready_for_api_pilot':
            api_pilot(root,directory,deadline)
        R.require(retained(root)==before and F.stopped(root)==profiles,'RESTART_RETENTION_DRIFT')
        R.save_json(directory/'summary.json',dict(classification='RESTART_REVIEW_COMPLETED',
            worker=worker.name,capacity_review=True,pilot_launched=proposal['decision']=='ready_for_api_pilot',
            automatic_runtime_successor=False,gate_acceptance=False))
        print(json.dumps({'evidence':str(directory),'capacity_review':True}))
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json',dict(classification='HELD',operation_replay=False,
                reason=str(error) if isinstance(error,R.RunnerError) else type(error).__name__))
        raise
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def resume_review(root,vault,old):
    """Review terminal repo repair; never re-launch its worker or rewrite its hold."""
    R.require(old.parent==root/'tmp/local-experiment/safe-restart' and old.name.startswith('restart-') and
              not old.is_symlink(),'RESTART_RESUME_SCOPE_INVALID')
    state=old.lstat()
    R.require(stat.S_ISDIR(state.st_mode) and state.st_uid==os.getuid() and
              stat.S_IMODE(state.st_mode)==0o700,'RESTART_RESUME_SCOPE_INVALID')
    original=R.private_json(old/'summary.json','RESTART_RESUME_UNVERIFIED')
    R.require(original==dict(classification='HELD',operation_replay=False,reason='SUPERVISOR_PROBE_FAILED'),
              'RESTART_RESUME_UNVERIFIED')
    before=S.baseline_file(old/'before.json')
    proposal=plan(R.private_json(old/'restart-plan.json','RESTART_PLAN_INVALID'))
    mission=R.private_json(old/'mission.json','RESTART_RESUME_UNVERIFIED')
    R.require(mission.get('gate_id')==GATE and mission.get('head_before')==before['head'],
              'RESTART_RESUME_UNVERIFIED')
    candidates=[]
    for worker in (root/'tmp/local-experiment/autonomous-runner').glob('gate-*'):
        result=worker/'attempt-001/result.json'
        if result.exists() and R.private_json(result,'RESTART_RESUME_UNVERIFIED').get('head_before')==before['head']:
            candidates.append(worker)
    R.require(len(candidates)==1,'RESTART_RESUME_WORKER_UNVERIFIED')
    worker=candidates[0]
    parent=root/'tmp/local-experiment/safe-restart-review';parent.mkdir(mode=0o700,exist_ok=True)
    state=parent.lstat()
    R.require(stat.S_ISDIR(state.st_mode) and state.st_uid==os.getuid() and stat.S_IMODE(state.st_mode)==0o700,
              'RESTART_RESUME_SCOPE_INVALID')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    directory=None;deadline=time.monotonic()+120*60
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        S.no_active_worker(root);S.check_resume_budget(root,0,60)
        summary,result,sid=S.terminal(root,worker)
        R.require(result['reason']=='no_safe_work','RESTART_RESUME_UNVERIFIED')
        head=R.checkout_head(root,vault)
        R.require(R.git(root,'merge-base','--is-ancestor',before['head'],result['head_after'])=='' and
                  R.git(root,'merge-base','--is-ancestor',result['head_after'],head)=='',
                  'RESTART_RESUME_HISTORY_INVALID')
        R.require(retained(root)==before['retained'] and F.stopped(root)==before['profiles'],
                  'RESTART_RETENTION_DRIFT')
        # No new worker/session or repair reservation; pilot's own reservations persist.
        directory=Path(tempfile.mkdtemp(prefix='review-',dir=parent))
        capacity_tests(root,directory)
        observed=review_capacity(root)
        R.save_json(directory/'capacity-review.json',dict(head=head,worker=worker.name,session=sid,
            original_series=old.name,original_summary=original,proposal=proposal,observations=observed,
            worker_replayed=False,model_calls=0,old_scopes_held=True,counters_reset=False,gate_acceptance=False))
        if proposal['decision']=='ready_for_api_pilot':api_pilot(root,directory,deadline)
        R.require(retained(root)==before['retained'] and F.stopped(root)==before['profiles'],
                  'RESTART_RETENTION_DRIFT')
        R.save_json(directory/'summary.json',dict(classification='RESTART_RESUME_REVIEW_COMPLETED',
            worker=worker.name,model_calls=0,worker_replayed=False,capacity_review=True,
            pilot_launched=proposal['decision']=='ready_for_api_pilot',automatic_runtime_successor=False,
            gate_acceptance=False))
        print(json.dumps({'evidence':str(directory),'model_calls':0,'capacity_review':True}))
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json',dict(classification='HELD',operation_replay=False,
                reason=str(error) if isinstance(error,R.RunnerError) else type(error).__name__))
        raise
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def test_planning(root,vault):
    """One repo-only planning session; no Go test/build or execution successor."""
    parent=root/'tmp/local-experiment/test-capacity-plan';parent.mkdir(mode=0o700,exist_ok=True)
    state=parent.lstat()
    R.require(stat.S_ISDIR(state.st_mode) and state.st_uid==os.getuid() and stat.S_IMODE(state.st_mode)==0o700,
              'TEST_PLAN_SCOPE_INVALID')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    directory=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        R.require(len(list(parent.glob('plan-*')))<2,'TEST_PLAN_APPROACH_EXHAUSTED')
        head=R.checkout_head(root,vault);S.no_active_worker(root);S.check_resume_budget(root,1,45)
        R.subscription_preflight('opencode',S.MODEL,root)
        profiles=F.stopped(root);before=retained(root)
        baseline=S.baseline_file(root/'tmp/local-experiment/safe-restart/restart-hr2zj9uf/before.json')
        R.require(before==baseline['retained'] and profiles==baseline['profiles'],'RESTART_RETENTION_DRIFT')
        directory=Path(tempfile.mkdtemp(prefix='plan-',dir=parent))
        R.save_json(directory/'before.json',dict(head=head,retained=before,profiles=profiles))
        gate='L0_FLUX_TEST_CAPACITY_PLAN'
        evidence=root/'tmp/local-experiment/safe-restart-review/review-mv27dels'
        for name in PRIOR+('gate-_g0u5c6u',):
            worker=root/'tmp/local-experiment/autonomous-runner'/name
            summary,result,sid=S.terminal(root,worker)
            receipt=dict(schema_version=1,source_run=name,original_summary=summary,original_result=result,
                checkpoint=R.private_json(worker/'attempt-001/checkpoint.json','CHECKPOINT_INVALID'),
                reviewed_head=head,last_session_id=sid,server_outcome='succeeded_idle_nonactive',
                allowed_gate=gate,ledger_sha256=before['security/resident-images.json'],
                completed_review_sha256=hashlib.sha256((evidence/'summary.json').read_bytes()).hexdigest(),
                pilot_review_sha256=hashlib.sha256((evidence/'pilot-review.json').read_bytes()).hexdigest(),
                decision='old_scope_held_test_capacity_planning_no_execution')
            path=root/'tmp/local-experiment/gate-batches'/f'test-plan-review-{name}.json'
            if not path.exists():R.save_json(path,receipt)
            R.require(R.reviewed_restart_stop(root,worker,summary,head,gate),'TEST_PLAN_PRIOR_INVALID')
        objective=dict(schema_version=1,wp_id='WP-L0-GITOPS-001',gate_id=gate,head_before=head,
            baseline='Capacity repair independently reviewed; retained builds below hard8GiB but bulk7GiB writes held. Isolated Go1.26.6 API12 tests/1package passed. Root/signature tests incomplete; two binary builds exhausted. All profiles Stopped, immutable failures preserved.',
            target_evidence='Investigate retained source/test receipts read-only. Implement a typed small-unit test plan and validation with regression tests, measured input/cache/temp/free-space budgets and fixed per-unit deadlines. Write owned0600 '+str((directory/'test-plan.json').relative_to(root))+': schema_version=1, decision=ready_for_parent_review|hold, notes30-350 chars. Provide detailed repo-relative plan/evidence; no execution in this worker.',
            allowed_scope='Repository-only tools/local_experiment/flux_test_plan.py, tests/test_flux_test_plan.py, matching design/WP/runner notes; small new diagnostic receipts in this plan scope. Inspect retained source/module/test outcomes read-only. Distinguish successful, known terminated and unknown operations; propose materially different bounded test units with isolated writable caches/short TMPDIR and parent approval of fixed argv.',
            stop_conditions='No Go invocation/tests/build/downloads, source refetch, binary/image/runtime execution, VM start, registry/publication writes, pins/ledger/approval/admission change, retained writes/deletion, retry-counter reset, third binary attempt, replay of unknown operations or blind replay of interrupted tests. Do not launch a runner or successor. No new spending/provider fallback.',
            recovery='Commit coherent planner/validator and focused Python tests. Preserve all original records and retained bytes. Parent reviews terminal worker, diff, fixed tests, budgets and proposals before selecting any test unit. This worker only plans; neither a commit nor ready plan authorizes execution or establishes full upstream/publisher/admission/L0 acceptance.')
        for key in ('baseline','target_evidence','allowed_scope','stop_conditions','recovery'):
            R.require(30<=len(objective[key])<=650,'MISSION_INVALID')
        R.save_json(directory/'mission.json',objective)
        R.gate_preflight(root,head,1,45,independent_gate=gate)
        reason,worker=R.run(root,vault,'opencode',max_runs=1,max_minutes=45,model=S.MODEL,mission=objective)
        summary,result,sid=S.terminal(root,worker)
        R.require(reason in ('run_budget_reached','agent_complete_claim','agent_no_safe_work_claim'),
                  'TEST_PLAN_STOP_UNCLASSIFIED')
        after=R.checkout_head(root,vault)
        changes=R.git(root,'diff','--name-only',head,after).splitlines()
        R.require(after!=head and all(p.startswith('docs/design/') or p in
            ('tools/local_experiment/flux_test_plan.py','tests/test_flux_test_plan.py',
             'tools/local_experiment/AUTONOMOUS_RUNNER.md') for p in changes),'TEST_PLAN_SCOPE_DRIFT')
        R.require(retained(root)==before and F.stopped(root)==profiles,'RESTART_RETENTION_DRIFT')
        value=R.private_json(directory/'test-plan.json','TEST_PLAN_INVALID')
        R.require(set(value)=={'schema_version','decision','notes'} and type(value['schema_version']) is int and
            value['schema_version']==1 and value['decision'] in ('ready_for_parent_review','hold') and
            isinstance(value['notes'],str) and 30<=len(value['notes'])<=350 and
            not any(ord(c)<32 for c in value['notes']),'TEST_PLAN_INVALID')
        completed=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests',
            '-p','test_flux_test_plan.py','-q'],cwd=root,capture_output=True,timeout=120)
        R.private_file(directory/'parent-planner-tests.stdout',completed.stdout)
        R.private_file(directory/'parent-planner-tests.stderr',completed.stderr)
        R.require(completed.returncode==0 and re.search(rb'Ran [1-9][0-9]* tests?\b',completed.stderr),
                  'TEST_PLAN_PARENT_TESTS_FAILED')
        R.save_json(directory/'summary.json',dict(classification='TEST_PLAN_PARENT_REVIEW_PENDING',
            worker=worker.name,session=sid,head=after,proposal=value,focused_tests_passed=True,
            test_execution_authorized=False,automatic_successor=False,gate_acceptance=False))
        print(json.dumps({'evidence':str(directory),'test_execution':False,'parent_review_pending':True}))
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            R.save_json(directory/'summary.json',dict(classification='HELD',operation_replay=False,
                reason=str(error) if isinstance(error,R.RunnerError) else type(error).__name__))
        raise
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--use-pro-weekly-limit',action='store_true')
    p.add_argument('--resume-series',type=Path,help='review a completed repository repair without re-launching it')
    p.add_argument('--test-plan',action='store_true',help='one repo-only small-unit test planning worker')
    args=p.parse_args()
    if not args.execute:
        print(json.dumps({'mode':'plan-only','active_calls':0,
            'first_session_minutes':0 if args.resume_series is not None else 45,'pilot_minutes':60}));return 0
    try:
        R.require(args.use_pro_weekly_limit,'BUDGET_REQUIRED')
        R.require(not (args.test_plan and args.resume_series is not None),'RESTART_MODE_INVALID')
        if args.test_plan:test_planning(R.ROOT,R.VAULT)
        elif args.resume_series is None:run(R.ROOT,R.VAULT)
        else:resume_review(R.ROOT,R.VAULT,args.resume_series)
        return 0
    except (R.RunnerError,OSError,ValueError,KeyError,RuntimeError,ImportError) as error:
        print(str(error) if isinstance(error,R.RunnerError) else 'RESTART_UNVERIFIED_STATE');return 1


if __name__=='__main__':raise SystemExit(main())
