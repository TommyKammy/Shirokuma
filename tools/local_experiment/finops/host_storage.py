"""Three fixed host references and bounded logical-file metadata measurements."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
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
MEASURED_LIMITS = ['THREE_FIXED_DIRECTORIES_ONLY_NOTEBOOK_WORKSPACE_MEASURED',
                   'WORKSPACE_LOGICAL_REGULAR_FILE_SIZE_NOT_ALLOCATED_OR_UNIQUE_PHYSICAL_BYTES',
                   'SYMLINK_HARDLINK_SPECIAL_AND_FOREIGN_DEVICE_REJECTED',
                   'BOUNDED_TWO_PASS_METADATA_SCAN_NOT_ATOMIC_OR_BACKUP_VALIDATION']
MLFLOW_LIMITS = ['THREE_FIXED_DIRECTORIES_ONLY_NOTEBOOK_WORKSPACE_AND_MLFLOW_STORE_MEASURED',
                 'MLFLOW_STORE_INCLUDES_SQLITE_SIDECARS_AND_ARTIFACTS_NOT_DB_CONSISTENCY',
                 'LOGICAL_REGULAR_FILE_SIZE_NOT_ALLOCATED_OR_UNIQUE_PHYSICAL_BYTES',
                 'SYMLINK_HARDLINK_SPECIAL_AND_FOREIGN_DEVICE_REJECTED',
                  'BOUNDED_TWO_PASS_METADATA_SCAN_NOT_ATOMIC_OR_BACKUP_VALIDATION']
SERVING_LIMITS = ['THREE_FIXED_DIRECTORIES_EACH_MEASURED_SEPARATELY_NOT_FULL_COMPONENT_STORAGE',
                  'SERVING_HOME_EXCLUDES_SIBLING_TMP_LOGS_AND_SHARED_MODEL_ARTIFACTS',
                  'LOGICAL_REGULAR_FILE_SIZE_NOT_ALLOCATED_OR_UNIQUE_PHYSICAL_BYTES',
                  'SYMLINK_HARDLINK_SPECIAL_AND_FOREIGN_DEVICE_REJECTED',
                  'BOUNDED_TWO_PASS_METADATA_SCAN_NOT_ATOMIC_OR_BACKUP_VALIDATION']
REASONS_V1 = frozenset(('READ_FAILED', 'READ_LIMIT', 'READ_TIMEOUT', 'CLEANUP_UNVERIFIED',
                        'SCOPE_INVALID', 'TARGET_MISSING', 'IDENTITY_CHANGED', 'SAMPLE_INVALID'))
REASONS = REASONS_V1 | {'TREE_CHANGED'}


class HostStorageError(ValueError): pass
class WorkerTimeout(BaseException): pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok: raise HostStorageError(reason)


def unknown(component, reason):
    require(component in TARGETS and reason in REASONS)
    return dict(component=component, state='unknown', reason=reason, directory=TARGETS[component],
                owner_sha256=None, directory_identity=None, request_started_at=None, observed_at=None,
                used_bytes=None, usage_reason='NOT_MEASURED')


def selected(component, version):
    return (component == 'notebook-lab' or
            (version >= 3 and component == 'mlflow') or
            (version >= 4 and component == 'model-serving'))


def measured_row(row, *, version=2):
    require(version in (2,3,4))
    row = deepcopy(row)
    measured = selected(row['component'], version)
    row.update(usage_state='unknown' if measured and row['state'] == 'unknown'
                else 'not_measured', usage_basis='logical_regular_file_size_sum' if measured else None,
                usage_reason=row['reason'] if measured and row['state'] == 'unknown' else 'NOT_MEASURED',
               regular_file_count=None,directory_count=None,usage_started_at=None,usage_observed_at=None)
    return row


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


def validate_row(row, *, version=1):
    require(version in (1,2,3,4))
    measured = selected(row.get('component'), version)
    require(type(row) is dict and row.get('component') in TARGETS and
            set(row) == set(measured_row(unknown('mlflow', 'READ_FAILED')) if version >= 2 else unknown('mlflow', 'READ_FAILED')) and
            row['directory'] == TARGETS[row['component']] and
             (row['used_bytes'] is None if version == 1 or not measured or
             row['state'] == 'unknown' else True) and
            (row['usage_reason'] == 'NOT_MEASURED' if version == 1 else True))
    if row['state'] == 'unknown':
        if version == 1:
            require(row['reason'] in REASONS_V1 and row == unknown(row['component'], row['reason']))
        else:
            expected = measured_row(unknown(row['component'], row['reason']),version=version)
            require(row == expected)
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
    if version >= 2 and row['state'] == 'observed':
        if not measured:
            require(row == measured_row({k:row[k] for k in unknown('mlflow','READ_FAILED')},version=version))
        else:
            require(row['usage_basis'] == 'logical_regular_file_size_sum' and
                    row['usage_state'] in ('observed','unknown'))
            if row['usage_state'] == 'observed':
                require(row['usage_reason'] is None and type(row['used_bytes']) is str and
                        len(row['used_bytes']) <= 19 and row['used_bytes'].isascii() and
                        row['used_bytes'].isdecimal() and str(int(row['used_bytes'])) == row['used_bytes'] and
                        int(row['used_bytes']) <= 2**63-1)
                for count in ('regular_file_count','directory_count'):
                    require(type(row[count]) is int and 0 <= row[count] <= 10000)
                require(row['regular_file_count'] + row['directory_count'] <= 10000 and
                        (row['used_bytes'] == '0' or row['regular_file_count'] > 0))
            else:
                require(row['usage_reason'] in REASONS and row['used_bytes'] is None and
                        row['regular_file_count'] is None and row['directory_count'] is None)
            if row['usage_started_at'] is None:
                require(row['usage_observed_at'] is None and row['usage_state'] == 'unknown')
            else:
                start, end = U.stamp(row['usage_started_at']), U.stamp(row['usage_observed_at'])
                reference_start, reference_end = U.stamp(row['request_started_at']), U.stamp(row['observed_at'])
                require(reference_start[1] <= start[1] <= end[1] <= reference_end[1] and
                        (row['usage_state'] == 'unknown' or end[1]-start[1] <= 10*10**9))
            if row['usage_state'] == 'observed': require(row['usage_started_at'] is not None)
    return deepcopy(row)


def validate(value):
    require(type(value) is dict and set(value) == {'schema_version','components','limits'} and
            type(value['schema_version']) is int and value['schema_version'] in (1,2,3,4) and
            value['limits'] == {1:LIMITS,2:MEASURED_LIMITS,3:MLFLOW_LIMITS,4:SERVING_LIMITS}[value['schema_version']] and type(value['components']) is list and
            len(value['components']) == len(TARGETS))
    require([r.get('component') for r in value['components']] == list(TARGETS))
    return dict(schema_version=value['schema_version'], components=[validate_row(r, version=value['schema_version'])
            for r in value['components']], limits=value['limits'][:])


def unknown_all(reason, *, version=1):
    require(version in (1,2,3,4))
    return dict(schema_version=version, components=[measured_row(unknown(n,reason),version=version) if version >= 2 else unknown(n,reason)
                for n in TARGETS], limits={1:LIMITS,2:MEASURED_LIMITS,3:MLFLOW_LIMITS,4:SERVING_LIMITS}[version][:])


def collect(backend, *, measure=False, measure_mlflow=False, measure_serving=False):
    require((not measure_mlflow or measure) and (not measure_serving or measure_mlflow))
    version = 4 if measure_serving else 3 if measure_mlflow else 2 if measure else 1
    rows = []
    for component in TARGETS:
        try:
            started = backend.now(); before = backend.snapshot(component)
            attempt_start = outcome = attempt_end = None
            scan = measure and selected(component,version)
            if scan:
                from tools.local_experiment.finops import workspace_usage as W
                failure = 'READ_FAILED'
                attempt_start = backend.now()
                try:
                    method = (backend.measure_mlflow if component == 'mlflow' else
                              backend.measure_serving if component == 'model-serving' else backend.measure_workspace)
                    outcome = method(int(before['directory_identity']['device']),
                                     int(before['directory_identity']['inode']))
                except Exception as error:
                    failure = str(error) if isinstance(error, (HostStorageError,W.UsageError)) and str(error) in REASONS else 'READ_FAILED'
                attempt_end = backend.now()
            after = backend.snapshot(component)
            row = project(component,before,after,request_started_at=started,observed_at=backend.now())
            if measure:
                row = measured_row(row,version=version)
                if scan:
                    row.update(usage_started_at=U.stamp(attempt_start)[0],usage_observed_at=U.stamp(attempt_end)[0])
                    if outcome is None:
                        row.update(usage_state='unknown',usage_reason=failure)
                    else:
                        row.update(usage_state='observed',usage_reason=None,**outcome)
                row = validate_row(row,version=version)
            rows.append(row)
        except Exception as error:
            code = str(error) if isinstance(error, HostStorageError) and str(error) in REASONS else 'READ_FAILED'
            rows.append(measured_row(unknown(component,code),version=version) if measure else unknown(component,code))
    return validate(dict(schema_version=version,components=rows,
                          limits={1:LIMITS,2:MEASURED_LIMITS,3:MLFLOW_LIMITS,4:SERVING_LIMITS}[version][:]))


class Backend:
    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    snapshot = staticmethod(snapshot)
    @staticmethod
    def measure_workspace(device, inode):
        from tools.local_experiment.finops import workspace_usage as W
        return W.measure(ROOT/TARGETS['notebook-lab'],device=device,inode=inode)
    @staticmethod
    def measure_mlflow(device, inode):
        from tools.local_experiment.finops import workspace_usage as W
        return W.measure(ROOT/TARGETS['mlflow'],device=device,inode=inode,
                          required_root_entries=(('tracking.sqlite','file'),('artifacts','directory')))
    @staticmethod
    def measure_serving(device, inode):
        from tools.local_experiment.finops import workspace_usage as W
        return W.measure(ROOT/TARGETS['model-serving'],device=device,inode=inode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measure-workspace',action='store_true')
    parser.add_argument('--measure-mlflow-store',action='store_true')
    parser.add_argument('--measure-serving-home',action='store_true')
    args=parser.parse_args(argv)
    original = None
    if args.measure_workspace or args.measure_mlflow_store or args.measure_serving_home:
        original = signal.getsignal(signal.SIGALRM)
        def timeout(*_): raise WorkerTimeout()
        signal.signal(signal.SIGALRM,timeout)
        signal.setitimer(signal.ITIMER_REAL,18)
    try:
        try: value = collect(Backend(),measure=args.measure_workspace or args.measure_mlflow_store or args.measure_serving_home,
                             measure_mlflow=args.measure_mlflow_store or args.measure_serving_home,
                             measure_serving=args.measure_serving_home)
        except WorkerTimeout: value = unknown_all('READ_TIMEOUT',version=4 if args.measure_serving_home else 3 if args.measure_mlflow_store else 2)
    finally:
        if original is not None:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,original)
    print(json.dumps(value,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')))
    return 0


if __name__ == '__main__': raise SystemExit(main())
