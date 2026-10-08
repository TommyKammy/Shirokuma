"""Stage public Flux2 release candidates and scan exact ARM64 bytes; never apply."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from tools.local_experiment import flux_refresh_scan as Scan

F = Scan.F
R, S = F.R, F.S


def release_images(raw, metadata, baseline):
    R.require(isinstance(metadata, dict) and
              re.fullmatch(r'v2\.\d+\.\d+', metadata.get('release', '')),
              'CANDIDATE_RELEASE_UNVERIFIED')
    refs = re.findall(r'image:\s*(ghcr\.io/fluxcd/(?:source|kustomize|helm|notification)-controller:v\d+\.\d+\.\d+)\s', raw.decode())
    R.require(len(refs) == 4 and refs == metadata.get('references'), 'CANDIDATE_TAGS_UNVERIFIED')
    prior = {x['component']:x for x in baseline}
    images=[]
    for reference in refs:
        name,version=reference.removeprefix('ghcr.io/fluxcd/').split(':')
        R.require(name in prior and name not in {x['component'] for x in images}, 'CANDIDATE_COMPONENT_UNVERIFIED')
        parts = lambda v: tuple(int(n) for n in v.removeprefix('v').split('.'))
        R.require(parts(version)[0] == parts(prior[name]['version'])[0] and
                  parts(version) >= parts(prior[name]['version']), 'CANDIDATE_DOWNGRADE_OR_MAJOR_CHANGE')
        images.append(dict(component=name, version=version, tag_reference=reference, platform='linux/arm64'))
    R.require({x['component'] for x in images} == S.FLUX, 'CANDIDATE_COMPONENT_UNVERIFIED')
    return images


def arm64_descriptor(index):
    R.require(isinstance(index, dict) and index.get('schemaVersion') == 2 and
              index.get('mediaType') in ('application/vnd.oci.image.index.v1+json',
                  'application/vnd.docker.distribution.manifest.list.v2+json'), 'CANDIDATE_INDEX_UNVERIFIED')
    matches = [x for x in index.get('manifests', []) if
               x.get('platform', {}).get('os') == 'linux' and
               x.get('platform', {}).get('architecture') == 'arm64' and
               x.get('platform', {}).get('variant') in (None, '', 'v8')]
    R.require(len(matches) == 1 and re.fullmatch(r'sha256:[a-f0-9]{64}', matches[0].get('digest', '')),
              'CANDIDATE_ARM64_BINDING_UNVERIFIED')
    return matches[0]['digest']


def collect(root, discovery):
    expected=root/'tmp/local-experiment/flux-pin-discovery'
    R.require(discovery.parent == expected and discovery.name.startswith('discovery-') and
              not discovery.is_symlink(), 'CANDIDATE_DISCOVERY_SCOPE_INVALID')
    st=discovery.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
              stat.S_IMODE(st.st_mode) == 0o700, 'CANDIDATE_DISCOVERY_SCOPE_INVALID')
    raw=(discovery/'install.yaml').read_bytes()
    R.require(0<len(raw)<=4*1024*1024, 'CANDIDATE_RELEASE_OUTPUT_LIMIT')
    metadata=R.private_json(discovery/'controller-tags.json', 'CANDIDATE_METADATA_UNVERIFIED')
    baseline,ledger_hash=F.inputs(root)
    images=release_images(raw,metadata,baseline)
    parent=root/'tmp/local-experiment/flux-pin-candidate'
    parent.mkdir(mode=0o700,exist_ok=True)
    st=parent.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
              stat.S_IMODE(st.st_mode) == 0o700, 'CANDIDATE_SCOPE_INVALID')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    directory=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # New approach, no resets of diagnosis/refresh reservations or starts.
        R.require(len(list(parent.glob('candidate-*'))) < 2, 'CANDIDATE_APPROACH_EXHAUSTED')
        S.no_active_worker(root)
        S.check_resume_budget(root,0,120)
        profiles=F.stopped(root)
        directory=Path(tempfile.mkdtemp(prefix='candidate-',dir=parent))
        os.umask(0o077)
        Scan.check_budget(directory)
        env=Scan.environment(directory)
        deadline=time.monotonic()+120*60
        Scan.save(directory/'intent.json',dict(release=metadata['release'],
            discovery=discovery.name, install_sha256=hashlib.sha256(raw).hexdigest(),
            images=images, ledger_sha256=ledger_hash, public_read_only=True,
            registry_writes=False, risk_approval=False, runtime_writes=False,
            started_at=Scan.timestamp()))
        for image in images:
            name=image['component'];repo='ghcr.io/fluxcd/'+name
            R.require(time.monotonic()<deadline, 'CANDIDATE_TIME_BUDGET_EXPIRED')
            # Resolve the tag once, then use only its immutable index and child.
            Scan.execute(directory,env,name+'-resolve',['crane','digest',image['tag_reference']],name+'.index-digest',deadline=deadline)
            digest=(directory/(name+'.index-digest')).read_text().strip()
            R.require(re.fullmatch(r'sha256:[a-f0-9]{64}',digest), 'CANDIDATE_INDEX_DIGEST_UNVERIFIED')
            image['index_reference']=repo+'@'+digest
            Scan.execute(directory,env,name+'-index',['crane','manifest',image['index_reference']],name+'.index.json',deadline=deadline)
            index=S.baseline_file(directory/(name+'.index.json'))
            child=arm64_descriptor(index)
            image['reference']=repo+'@'+child
            Scan.execute(directory,env,name+'-child-digest',['crane','digest',image['reference']],name+'.child-digest',deadline=deadline)
            R.require((directory/(name+'.child-digest')).read_text().strip()==child,
                      'CANDIDATE_CHILD_DIGEST_UNVERIFIED')
        Scan.save(directory/'candidate-inputs.json',dict(release=metadata['release'],images=images,
            publisher_verified=False, ledger_changed=False))
        plan=list(Scan.commands(directory,images))
        started=time.time()
        for name,argv,output in plan:
            R.require(time.monotonic()<deadline, 'CANDIDATE_TIME_BUDGET_EXPIRED')
            Scan.execute(directory,env,name,argv,output,deadline=deadline)
        observed=F.verify_scans(directory,images,started)
        R.require(F.inputs(root)[1]==ledger_hash and F.stopped(root)==profiles, 'CANDIDATE_SCOPE_DRIFT')
        clean=all(x['high']==0 and x['critical']==0 for x in observed['scans'])
        Scan.save(directory/'summary.json',dict(classification='CANDIDATE_SCAN_EVIDENCE_ONLY',
            release=metadata['release'], observations=observed, all_high_critical_zero=clean,
            publisher_verified=False, admission=False, runtime_unchanged=True,
            gate_acceptance=False, generated_bytes=Scan.usage(directory)))
        return directory
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            Scan.save(directory/'summary.json',dict(classification='CANDIDATE_DEPENDENCY_HELD',
                reason=str(error) if isinstance(error,R.RunnerError) else type(error).__name__,
                operation_replay=False, admission=False, gate_acceptance=False))
        raise
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--discovery',type=Path)
    args=p.parse_args()
    if not args.execute:
        print(json.dumps({'mode':'plan-only','model_calls':0,'runtime_writes':False}))
        return 0
    try:
        R.require(args.discovery is not None, 'CANDIDATE_DISCOVERY_REQUIRED')
        d=collect(R.ROOT,args.discovery)
        print(json.dumps({'evidence':str(d),'admission':False}))
        return 0
    except (R.RunnerError,OSError,ValueError,KeyError) as error:
        print(str(error) if isinstance(error,R.RunnerError) else 'CANDIDATE_UNVERIFIED_STATE')
        return 1


if __name__=='__main__': raise SystemExit(main())
