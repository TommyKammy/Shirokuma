"""Three fixed host directory references; read-only, not byte usage."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools.local_experiment.gateway import pod_usage as U

TARGETS = {'mlflow': 'tmp/local-experiment/mlflow-tracking/store',
           'notebook-lab': 'tmp/local-experiment/notebook-lab/workspace',
           'model-serving': 'tmp/local-experiment/ridge-serving/home'}
LIMITS = ['THREE_FIXED_DIRECTORIES_NOT_ALL_HOST_STORAGE',
          'DIRECTORY_REFERENCE_NOT_FILE_OWNERSHIP_OR_EXCLUSIVE_USAGE',
          'DIRECTORY_USED_BYTES_AND_PHYSICAL_OCCUPANCY_NOT_MEASURED',
          'NO_RECURSIVE_TRAVERSAL_OR_BACKUP_VALIDATION']
REASONS = frozenset(('READ_FAILED', 'READ_LIMIT', 'READ_TIMEOUT', 'CLEANUP_UNVERIFIED',
                     'SCOPE_INVALID', 'TARGET_MISSING', 'IDENTITY_CHANGED', 'SAMPLE_INVALID'))


class HostStorageError(ValueError): pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok: raise HostStorageError(reason)


def unknown(component, reason):
    require(component in TARGETS and reason in REASONS)
    return dict(component=component, state='unknown', reason=reason, directory=TARGETS[component],
                owner_sha256=None, directory_identity=None, request_started_at=None, observed_at=None,
                used_bytes=None, usage_reason='NOT_MEASURED')


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _directory(path):
    require(path.is_absolute() and ROOT in path.parents and not any(p.is_symlink() for p in (path,*path.parents)),
            'SCOPE_INVALID')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == 0o700, 'SCOPE_INVALID')
    return dict(device=str(info.st_dev), inode=str(info.st_ino))


def snapshot(component):
    require(component in TARGETS)
    # Imports reuse the existing private ownership/config validators. Do not
    # call status/start, enumerate directory trees, or open saved user data.
    from tools.local_experiment.mlflow import runtime as M
    from tools.local_experiment.mlflow import serving_runtime as S
    from tools.local_experiment.notebook_lab import runtime as L
    if component == 'mlflow':
        marker = M.require_store()
        require(marker['backend_uri'] == 'sqlite:///' + str(M.STORE/'tracking.sqlite') and
                marker['artifact_root'] == str(M.ARTIFACT_ROOT) and M.STORE == ROOT/TARGETS[component])
        owner = {'store_id':marker['store_id'], 'owner':marker['owner'],
                 'backend_uri':marker['backend_uri'],'artifact_root':marker['artifact_root']}
    elif component == 'notebook-lab':
        owner = L._owner()
        require(L.STATE/'workspace' == ROOT/TARGETS[component] and
                L._config(L._token())['ServerApp']['root_dir'] == str(L.STATE/'workspace'))
    else:
        owner = S._owner()
        require(S.STATE/'home' == ROOT/TARGETS[component] and S.environment()['HOME'] == str(S.STATE/'home'))
    return dict(owner_sha256=_digest(owner), directory_identity=_directory(ROOT/TARGETS[component]))


def project(component, before, after, *, request_started_at, observed_at):
    require(component in TARGETS and type(before) is dict and before == after, 'IDENTITY_CHANGED')
    start, end = U.stamp(request_started_at), U.stamp(observed_at)
    require(0 <= end[1]-start[1] <= 30*10**9, 'SAMPLE_INVALID')
    row = unknown(component, 'READ_FAILED')
    row.update(state='observed', reason=None, owner_sha256=before.get('owner_sha256'),
               directory_identity=before.get('directory_identity'),
               request_started_at=start[0], observed_at=end[0])
    return validate_row(row)


def validate_row(row):
    require(type(row) is dict and row.get('component') in TARGETS and
            set(row) == set(unknown('mlflow', 'READ_FAILED')) and
            row['directory'] == TARGETS[row['component']] and row['used_bytes'] is None and
            row['usage_reason'] == 'NOT_MEASURED')
    if row['state'] == 'unknown':
        require(row == unknown(row['component'], row['reason']))
    else:
        require(row['state'] == 'observed' and row['reason'] is None and
                type(row['owner_sha256']) is str and len(row['owner_sha256']) == 64 and
                all(c in '0123456789abcdef' for c in row['owner_sha256']) and
                type(row['directory_identity']) is dict and
                set(row['directory_identity']) == {'device','inode'})
        for value in row['directory_identity'].values():
            require(type(value) is str and len(value) <= 20 and value.isascii() and value.isdecimal() and int(value) > 0)
        start, end = U.stamp(row['request_started_at']), U.stamp(row['observed_at'])
        require(0 <= end[1]-start[1] <= 30*10**9)
    return deepcopy(row)


def validate(value):
    require(type(value) is dict and set(value) == {'schema_version','components','limits'} and
            type(value['schema_version']) is int and value['schema_version'] == 1 and
            value['limits'] == LIMITS and type(value['components']) is list and
            len(value['components']) == len(TARGETS))
    require([r.get('component') for r in value['components']] == list(TARGETS))
    return dict(schema_version=1, components=[validate_row(r) for r in value['components']], limits=LIMITS[:])


def unknown_all(reason):
    return dict(schema_version=1, components=[unknown(n,reason) for n in TARGETS], limits=LIMITS[:])


def collect(backend):
    rows = []
    for component in TARGETS:
        try:
            started = backend.now(); before = backend.snapshot(component)
            after = backend.snapshot(component)
            rows.append(project(component,before,after,request_started_at=started,observed_at=backend.now()))
        except Exception as error:
            code = str(error) if isinstance(error, HostStorageError) and str(error) in REASONS else 'READ_FAILED'
            rows.append(unknown(component,code))
    return validate(dict(schema_version=1,components=rows,limits=LIMITS[:]))


class Backend:
    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    snapshot = staticmethod(snapshot)


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(json.dumps(collect(Backend()),ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')))
    return 0


if __name__ == '__main__': raise SystemExit(main())
