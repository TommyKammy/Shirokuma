"""Offline local image evidence from an immutable upstream image and patched binary.

No image execution, daemon, registry write, pin change or admission decision.
"""
import argparse
from datetime import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import time

from tools.local_experiment import flux_local_build_supervisor as B

R, S, P = B.R, B.S, B.P
TAG = 'shirokuma-local/source-controller:v1.9.6-go1.26.6-experimental'


def scan_observation(scan,scanner,started):
    def stamp(value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        R.require(parsed.tzinfo is not None,'LOCAL_IMAGE_TIME_UNVERIFIED')
        return parsed.timestamp()
    R.require(-120<=time.time()-stamp(scanner.get('VulnerabilityDB',{}).get('UpdatedAt'))<=48*3600 and
              started-120<=stamp(scan.get('CreatedAt'))<=time.time()+120 and
              scan.get('Trivy',{}).get('Version')==scanner.get('Version'),'LOCAL_IMAGE_FRESHNESS_UNVERIFIED')
    config=scan.get('Metadata',{}).get('ImageConfig',{})
    R.require(scan.get('Results') and config.get('architecture')=='arm64' and config.get('os')=='linux' and
              any(result.get('Class')=='os-pkgs' and result.get('Packages') for result in scan['Results']) and
              any(result.get('Type')=='gobinary' and result.get('Target')=='usr/local/bin/source-controller' and
                  any(pkg.get('Name')=='stdlib' and pkg.get('Version') in ('1.26.6','v1.26.6')
                      for pkg in result.get('Packages',[])) for result in scan['Results']),
              'LOCAL_IMAGE_SCAN_UNVERIFIED')
    findings=[v for result in scan['Results'] for v in result.get('Vulnerabilities',[])]
    return {severity:sum(v.get('Severity')==severity for v in findings)
            for severity in ('HIGH','CRITICAL','MEDIUM','LOW','UNKNOWN')}


def relabel_archive(source, destination, binary_hash):
    """Rewrite only metadata, keeping layer bytes; never extract or run a member."""
    with tarfile.open(source,'r:*') as archive:
        members={m.name:m for m in archive.getmembers()}
        R.require(len(members)==len(archive.getmembers()),'LOCAL_IMAGE_DUPLICATE_MEMBER')
        def raw(name):
            member=members[name]
            R.require(member.isfile() and member.size<=4*1024*1024,'LOCAL_IMAGE_METADATA_UNVERIFIED')
            return archive.extractfile(member).read()
        manifest=json.loads(raw('manifest.json'))
        R.require(isinstance(manifest,list) and len(manifest)==1,'LOCAL_IMAGE_MANIFEST_UNVERIFIED')
        entry=manifest[0];config=json.loads(raw(entry['Config']))
        R.require(config.get('architecture')=='arm64' and config.get('os')=='linux' and
                  config['config']['Entrypoint']==['source-controller'] and
                  config['config']['User']=='65534:65534' and
                  len(config['rootfs']['diff_ids'])==len(entry['Layers']),'LOCAL_IMAGE_CONFIG_UNVERIFIED')
        labels=config['config'].setdefault('Labels',{})
        labels.update({'org.opencontainers.image.version':'v1.9.6-shirokuma-go1.26.6',
            'org.opencontainers.image.description':'Unsigned Shirokuma local patched experiment; not upstream release',
            'io.shiro.local.binary-sha256':binary_hash,'io.shiro.local.publisher':'unverified-local-build'})
        data=json.dumps(config,sort_keys=True,separators=(',',':')).encode()
        config_name=hashlib.sha256(data).hexdigest()+'.json'
        entry['Config']=config_name;entry['RepoTags']=[TAG]
        with tarfile.open(destination,'x') as result:
            for name in entry['Layers']:
                member=members[name]
                R.require(member.isfile() and not Path(name).is_absolute() and '..' not in Path(name).parts,
                          'LOCAL_IMAGE_LAYER_UNVERIFIED')
                result.addfile(member,archive.extractfile(member))
            for name,content in ((config_name,data),('manifest.json',json.dumps(manifest).encode())):
                info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o600
                result.addfile(info,io.BytesIO(content))
    os.chmod(destination,0o600)
    return {'config_sha256':hashlib.sha256(data).hexdigest(),'upstream_publisher_claim':False}


def verified_binary(root):
    old=root/'tmp/local-experiment/flux-local-build/build-don5ufj3'
    summary,result,sid=S.terminal(root,root/'tmp/local-experiment/autonomous-runner/gate-ayqdekwa')
    R.require(result['reason']=='no_safe_work','LOCAL_IMAGE_BUILD_UNVERIFIED')
    record=S.baseline_file(old/'build-observation.json')
    binary=B.verify_binary(old/'source-controller')
    for name in ('source-controller','binary-metadata.stdout','binary-sbom.stdout','binary-sbom-scan.stdout'):
        R.require(record['hashes'][name]==hashlib.sha256((old/name).read_bytes()).hexdigest(),
                  'LOCAL_IMAGE_BUILD_HASH_UNVERIFIED')
    metadata=(old/'binary-metadata.stdout').read_bytes()
    R.require(b'go1.26.6' in metadata and b'GOOS=linux' in metadata and b'GOARCH=arm64' in metadata,
              'LOCAL_IMAGE_BUILD_METADATA_UNVERIFIED')
    scan=S.baseline_file(old/'binary-sbom-scan.stdout')
    R.require(scan.get('Results') and all(v.get('Severity') not in ('HIGH','CRITICAL')
              for result in scan['Results'] for v in result.get('Vulnerabilities',[])),
              'LOCAL_IMAGE_BINARY_SCAN_REJECTED')
    return old/'source-controller',binary,{'session':sid,'tests':'new-toolchain tests incomplete; old failures held'}


def collect(root):
    parent=root/'tmp/local-experiment/flux-local-image';parent.mkdir(mode=0o700,exist_ok=True)
    st=parent.lstat()
    R.require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o700,
              'LOCAL_IMAGE_SCOPE_INVALID')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    directory=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        R.require(len(list(parent.glob('image-*')))<2,'LOCAL_IMAGE_APPROACH_EXHAUSTED')
        S.no_active_worker(root);S.check_resume_budget(root,0,120)
        profiles=P.F.stopped(root);_,ledger_hash=P.F.inputs(root)
        binary,identity,build=verified_binary(root)
        candidate=R.private_json(root/'tmp/local-experiment/flux-pin-candidate/candidate-hn94027w/candidate-inputs.json',
                                 'LOCAL_IMAGE_BASE_UNVERIFIED')
        base=[x for x in candidate['images'] if x['component']=='source-controller']
        R.require(len(base)==1,'LOCAL_IMAGE_BASE_UNVERIFIED')
        directory=Path(tempfile.mkdtemp(prefix='image-',dir=parent));os.umask(0o077)
        P.Scan.check_budget(directory);env=P.Scan.environment(directory);deadline=time.monotonic()+120*60
        started=time.time()
        P.Scan.save(directory/'intent.json',dict(base=base[0],binary=identity,build=build,
            method='append fixed binary overlay then correct local attribution',model_calls=0,
            runtime_writes=False,registry_writes=False,risk_approval=False,
            tests_complete=False,ledger_sha256=ledger_hash,started_at=P.Scan.timestamp()))
        layer=directory/'patched-binary-layer.tar'
        with tarfile.open(layer,'x') as archive:
            info=tarfile.TarInfo('usr/local/bin/source-controller');info.size=binary.stat().st_size
            info.mode=0o755;info.uid=info.gid=0;info.mtime=0
            with binary.open('rb') as stream:archive.addfile(info,stream)
        os.chmod(layer,0o600)
        def execute(name,argv,output):P.Scan.execute(directory,env,name,argv,output,deadline=deadline)
        # --output saves locally; no push/load/import command is ever constructed.
        execute('append-local',['crane','append','--platform','linux/arm64','--base',base[0]['reference'],
                '--new_layer',str(layer),'--new_tag',TAG,'--output',str(directory/'unattributed.tar')],'append.stdout')
        attribution=relabel_archive(directory/'unattributed.tar',directory/'source-controller.tar',identity['sha256'])
        execute('local-digest',['crane','digest',TAG,'--tarball',str(directory/'source-controller.tar')],'image-digest.stdout')
        trivy=['trivy','--cache-dir',str(directory/'scan-work/cache/trivy'),'--config',str(directory/'scan-work/trivy.yaml')]
        execute('db-refresh',trivy+['image','--download-db-only','--no-progress','--skip-version-check',
                '--db-repository','ghcr.io/aquasecurity/trivy-db:2'],'db-refresh.stdout')
        execute('scanner-version',trivy+['--version','--format','json'],'scanner-version.json')
        execute('full-image-scan',trivy+['image','--input',str(directory/'source-controller.tar'),
                '--scanners','vuln','--list-all-pkgs','--format','json','--skip-db-update','--skip-java-db-update',
                '--skip-version-check','--no-progress','--timeout','15m'],'image.trivy.json')
        execute('image-sbom',['syft','scan','docker-archive:'+str(directory/'source-controller.tar'),
                '--config',str(directory/'scan-work/syft.yaml'),'--output','cyclonedx-json'],'image.sbom.json')
        scan=S.baseline_file(directory/'image.trivy.json')
        scanner=R.private_json(directory/'scanner-version.json','LOCAL_IMAGE_DB_UNVERIFIED')
        counts=scan_observation(scan,scanner,started)
        R.require(P.F.inputs(root)[1]==ledger_hash and P.F.stopped(root)==profiles,'LOCAL_IMAGE_SCOPE_DRIFT')
        P.Scan.check_budget(directory)
        P.Scan.save(directory/'summary.json',dict(classification='LOCAL_IMAGE_SCAN_EVIDENCE_ONLY',
            binary_sha256=identity['sha256'],attribution=attribution,severity_counts=counts,
            local_image_digest=(directory/'image-digest.stdout').read_text().strip(),
            archive_sha256=hashlib.sha256((directory/'source-controller.tar').read_bytes()).hexdigest(),
            publisher_verified=False,tests_complete=False,admission=False,gate_acceptance=False,
            runtime_unchanged=True,model_calls=0,generated_bytes=P.Scan.usage(directory)))
        return directory
    except Exception as error:
        if directory is not None and not (directory/'summary.json').exists():
            P.Scan.save(directory/'summary.json',dict(classification='LOCAL_IMAGE_DEPENDENCY_HELD',
                reason=str(error) if isinstance(error,R.RunnerError) else type(error).__name__,
                operation_replay=False,admission=False))
        raise
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def reobserve(root,directory):
    """Resolve only a completed scanner-format hold, without retrieval/assembly."""
    R.require(directory.parent==root/'tmp/local-experiment/flux-local-image' and
              directory.name.startswith('image-') and not directory.is_symlink(),'LOCAL_IMAGE_SCOPE_INVALID')
    original=R.private_json(directory/'summary.json','LOCAL_IMAGE_ORIGINAL_UNVERIFIED')
    R.require(original.get('classification')=='LOCAL_IMAGE_DEPENDENCY_HELD' and
              original.get('reason')=='LOCAL_IMAGE_SCAN_UNVERIFIED','LOCAL_IMAGE_ORIGINAL_UNVERIFIED')
    fd=os.open(root/'tmp/local-experiment/gate-supervisor/lock',os.O_RDWR|os.O_NOFOLLOW)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        S.no_active_worker(root);S.check_resume_budget(root,0,10)
        profiles=P.F.stopped(root);_,binary,_=verified_binary(root)
        intent=R.private_json(directory/'intent.json','LOCAL_IMAGE_INPUT_UNVERIFIED')
        R.require(intent['binary']['sha256']==binary['sha256'] and
                  intent['ledger_sha256']==P.F.inputs(root)[1],'LOCAL_IMAGE_INPUT_CHANGED')
        for operation,output in (('full-image-scan','image.trivy.json'),('image-sbom','image.sbom.json'),
                                 ('local-digest','image-digest.stdout'),('scanner-version','scanner-version.json')):
            receipt=R.private_json(directory/(operation+'.finished.json'),'LOCAL_IMAGE_OPERATION_UNVERIFIED')
            R.require(receipt.get('returncode')==0 and receipt.get('disposition')=='succeeded' and
                      receipt.get('output_sha256')==hashlib.sha256((directory/output).read_bytes()).hexdigest(),
                      'LOCAL_IMAGE_OPERATION_UNVERIFIED')
        scan=S.baseline_file(directory/'image.trivy.json')
        counts=scan_observation(scan,R.private_json(directory/'scanner-version.json','LOCAL_IMAGE_DB_UNVERIFIED'),
                                datetime.fromisoformat(intent['started_at']).timestamp())
        R.require(scan['Metadata']['ImageConfig']['config']['Labels'].get('io.shiro.local.binary-sha256')==binary['sha256'],
                  'LOCAL_IMAGE_ATTRIBUTION_UNVERIFIED')
        observed_digest=S.command(['crane','digest',TAG,'--tarball',str(directory/'source-controller.tar')],root,timeout=60).decode().strip()
        R.require(observed_digest==(directory/'image-digest.stdout').read_text().strip(),
                  'LOCAL_IMAGE_ARCHIVE_CHANGED')
        P.Scan.check_budget(directory)
        result=dict(classification='PARENT_VERIFIED_LOCAL_IMAGE_SCAN_EVIDENCE_ONLY',original_summary=original,
            severity_counts=counts,archive_sha256=hashlib.sha256((directory/'source-controller.tar').read_bytes()).hexdigest(),
            local_image_digest=(directory/'image-digest.stdout').read_text().strip(),binary_sha256=binary['sha256'],
            runtime_unchanged=True,profiles=profiles,operation_replay=False,model_calls=0,
            tests_complete=False,publisher_verified=False,admission=False,gate_acceptance=False)
        R.save_json(directory/'parent-resolution.json',result)
        return result
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');args=p.parse_args()
    if not args.execute:
        print(json.dumps({'mode':'plan-only','model_calls':0,'runtime_writes':False}));return 0
    try:
        d=collect(R.ROOT);print(json.dumps({'evidence':str(d),'admission':False}));return 0
    except (R.RunnerError,OSError,ValueError,KeyError) as error:
        print(str(error) if isinstance(error,R.RunnerError) else 'LOCAL_IMAGE_UNVERIFIED_STATE');return 1


if __name__=='__main__':raise SystemExit(main())
