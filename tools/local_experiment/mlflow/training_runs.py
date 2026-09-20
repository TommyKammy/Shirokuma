#!/usr/bin/env python3
"""Discover saved training tokens for review and prediction, without services.

Verified runs sort by kind, descending finished_ms, then training token.
Unavailable candidates sort by kind/token and never join the verified list.
Partial means a required result/workload file is absent. Invalid means the
supported saved-review contract was not verified; it includes unfinished or
failed receipts and does not assert that their files are corrupt.
No model ranking, dataset rows, coefficients or held-out rows are published.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from tools.local_experiment.notebook import training as T

PROFILE = 'saved-training-runs-v1'
MAX_CANDIDATES, MAX_PUBLIC_BYTES = 128, 256*1024
KINDS = ('iceberg','synthetic')
TOKEN = re.compile(r'[0-9a-f]{32}')
SCOPE = ('Individually validated saved training snapshots for explicit token selection; '
         'invalid means not verified, not necessarily corrupt; '
         'no live source read, model ranking, Registry or general quality claim.')
ERROR_CODES = {'TRAINING_RUNS_ARGUMENTS_INVALID','TRAINING_RUNS_ROOT_INVALID',
    'TRAINING_RUNS_INVENTORY_INVALID','TRAINING_RUNS_INVENTORY_CHANGED',
    'TRAINING_RUNS_TOKEN_COLLISION','TRAINING_RUNS_ENTRY_LIMIT','TRAINING_RUNS_PUBLIC_LIMIT',
    'TRAINING_RUNS_FAILED','TRAINING_RUNS_INTERRUPTED'}
IDENTITY_KEYS = ('run_id','experiment_id','experiment','store_id','workload_sha256','finished_ms')


class TrainingRunsError(ValueError):
    """Only fixed reason codes cross the standalone CLI boundary."""


def require(condition,code):
    if not condition: raise TrainingRunsError(code)


def _keep(first,later):
    return later if first is None or isinstance(first,Exception) and not isinstance(later,Exception) else first


def _signature(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _owned_directory(info):
    return stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700


def _parents():
    root=T.STATE
    require(root.is_absolute(),'TRAINING_RUNS_ROOT_INVALID')
    values=[]
    for path in (root,*root.parents):
        try: info=path.lstat()
        except OSError: raise TrainingRunsError('TRAINING_RUNS_ROOT_INVALID') from None
        require(stat.S_ISDIR(info.st_mode),'TRAINING_RUNS_ROOT_INVALID')
        if path==root: require(_owned_directory(info),'TRAINING_RUNS_ROOT_INVALID')
        values.append((str(path),info.st_dev,info.st_ino,info.st_mode,info.st_uid))
    return values


def _scan(path,remaining):
    """Enumerate through an owned no-follow directory descriptor, never links."""
    try: before=path.lstat()
    except FileNotFoundError: return None
    require(_owned_directory(before),'TRAINING_RUNS_ROOT_INVALID')
    fd,iterator,first,entries=None,None,None,{}
    try:
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        require(_signature(os.fstat(fd))==_signature(before),'TRAINING_RUNS_INVENTORY_CHANGED')
        iterator=os.scandir(fd)
        for entry in iterator:
            require(TOKEN.fullmatch(entry.name) is not None,'TRAINING_RUNS_INVENTORY_INVALID')
            require(entry.name not in entries,'TRAINING_RUNS_INVENTORY_INVALID')
            require(len(entries)<remaining,'TRAINING_RUNS_ENTRY_LIMIT')
            entries[entry.name]=_signature(entry.stat(follow_symlinks=False))
        require(_signature(os.fstat(fd))==_signature(before)
                and _signature(path.lstat())==_signature(before),'TRAINING_RUNS_INVENTORY_CHANGED')
    except BaseException as error: first=error
    finally:
        if iterator is not None:
            try: iterator.close()
            except BaseException as error: first=_keep(first,error)
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: first=_keep(first,error)
    if first is not None: raise first
    return {'signature':_signature(before),'entries':entries}


def inventory():
    """Two fixed namespaces only; unknown names or duplicated tokens fail all."""
    parents=_parents()
    values,seen={},set()
    for kind in KINDS:
        value=_scan(T.STATE/T.profile(kind)['folder'],MAX_CANDIDATES-len(seen))
        values[kind]=value
        names=set() if value is None else set(value['entries'])
        require(not seen.intersection(names),'TRAINING_RUNS_TOKEN_COLLISION')
        seen.update(names)
    require(_parents()==parents,'TRAINING_RUNS_INVENTORY_CHANGED')
    return {'parents':parents,'roots':values}


def project(public,token,kind):
    T.validate_public(public)
    require(public['profile']==T.profile(kind)['profile']
            and public['identity']['attempt_token']==token,'TRAINING_RUNS_INVENTORY_INVALID')
    return dict(kind=kind,training_token=token,
        identity={key:public['identity'][key] for key in IDENTITY_KEYS},
        test_metrics=deepcopy(public['metrics']['test']),
        baseline_metrics=deepcopy(public['metrics']['baseline_test']),
        lake_source=deepcopy(public['lake_source']) if kind=='iceberg' else None)


def _missing_receipt(directory,signature):
    fd,first,missing=None,None,False
    try:
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        require(_signature(os.fstat(fd))==signature,'TRAINING_RUNS_INVENTORY_CHANGED')
        for name in ('result.json','workload.json'):
            try: os.stat(name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError: missing=True
    except BaseException as error: first=error
    finally:
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: first=_keep(first,error)
    if first is not None: raise first
    return missing


def _report(runs,unavailable,*,error_code=None):
    require(error_code is None or error_code in ERROR_CODES,'TRAINING_RUNS_FAILED')
    return dict(schema_version=1,profile=PROFILE,result='complete' if error_code is None else 'incomplete',
        runs=runs,unavailable=unavailable,
        counts=dict(candidates=len(runs)+len(unavailable) if error_code is None else None,
                    verified=len(runs),unavailable=len(unavailable)),
        error_code=error_code,scope=SCOPE)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')+b'\n'


def collect():
    before=inventory()
    runs,unavailable=[],[]
    first=None
    try:
        for kind in KINDS:
            root=before['roots'][kind]
            if root is None: continue
            for token,signature in sorted(root['entries'].items()):
                path=T.STATE/T.profile(kind)['folder']/token
                # Signature fields are the lstat values; invalid candidate links
                # are reported without following them or invoking the loader.
                valid=stat.S_ISDIR(signature[2]) and stat.S_IMODE(signature[2])==0o700 and signature[3]==os.getuid()
                if not valid:
                    unavailable.append(dict(kind=kind,training_token=token,status='invalid',reason='TRAINING_RUN_INVALID'))
                    continue
                try:
                    if _missing_receipt(path,signature):
                        unavailable.append(dict(kind=kind,training_token=token,status='partial',reason='TRAINING_RUN_PARTIAL'))
                        continue
                    public=T.load_saved_review(token,kind=kind)
                    runs.append(project(public,token,kind))
                except Exception:
                    unavailable.append(dict(kind=kind,training_token=token,status='invalid',reason='TRAINING_RUN_INVALID'))
    except BaseException as error: first=error
    finally:
        try: require(inventory()==before,'TRAINING_RUNS_INVENTORY_CHANGED')
        except BaseException as error: first=_keep(first,error)
    if first is not None: raise first
    runs.sort(key=lambda row:(row['kind'],-row['identity']['finished_ms'],row['training_token']))
    unavailable.sort(key=lambda row:(row['kind'],row['training_token']))
    result=_report(runs,unavailable)
    require(len(canonical(result))<=MAX_PUBLIC_BYTES,'TRAINING_RUNS_PUBLIC_LIMIT')
    return result


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    first=None
    try:
        require(argv==[],'TRAINING_RUNS_ARGUMENTS_INVALID')
        result=collect()
    except BaseException as error:
        first=error
        code=str(error) if isinstance(error,TrainingRunsError) and str(error) in ERROR_CODES else (
            'TRAINING_RUNS_INTERRUPTED' if not isinstance(error,Exception) else 'TRAINING_RUNS_FAILED')
        result=_report([],[],error_code=code)
    try: print(canonical(result).decode('ascii'),end='')
    except BaseException as error: first=_keep(first,error)
    if first is not None and not isinstance(first,Exception): raise first
    if first is not None and result['result']=='complete': raise first
    return 0 if result['result']=='complete' else 1


if __name__=='__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
