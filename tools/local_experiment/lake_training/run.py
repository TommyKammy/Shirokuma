#!/usr/bin/env python3
"""One owned disposable Iceberg read, stopped-store backup and tracked fit."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.lake_training import fixture as F
from tools.local_experiment.lake_training import tracking as T
from tools.local_experiment.mlflow import backup as B, runtime as R
from tools.local_experiment.mlflow import lake_training_workload as L
from tools.local_experiment.notebook import client as N, selection as S
from tools.local_experiment.notebook_assets import run as A

STATE = ROOT / 'tmp/local-experiment/lake-training'
PROFILE = 'fixed-iceberg-training-pipeline-v1'
TRACKER_SECONDS, TRACKER_GRACE_SECONDS = 240, 60
ENV_KEYS = A.ENV_KEYS
RESTORE_RUNTIME_CODES = frozenset({
    'OWNED_SERVER_UNHEALTHY', 'SERVER_NOT_STOPPED', 'PORT_UNAVAILABLE',
    'START_INTENT_CONFLICT', 'RUNTIME_RECORD_CONFLICT', 'STORE_MARKER_CONFLICT',
    'PROCESS_INSPECTION_TOO_LARGE', 'PROCESS_INSPECTION_FAILED', 'PROCESS_INSPECTION_INVALID',
    'LISTENER_INSPECTION_FAILED', 'LISTENER_INSPECTION_INVALID',
    'PROCESS_IDENTITY_CONFLICT', 'PROCESS_GROUP_CONFLICT',
    'START_IDENTITY_UNCONFIRMED', 'START_IDENTITY_CONFLICT', 'SERVER_EXITED', 'START_TIMEOUT',
    'OWNED_PROCESS_MISSING', 'STOP_PROCESS_GROUP_CHANGED', 'STOP_PROCESS_IDENTITY_CHANGED', 'STOP_TIMEOUT',
})


class PipelineError(ValueError):
    """Fixed codes only; rows and subprocess bodies never become error text."""


def require(condition, code='LAKE_PIPELINE_FAILED'):
    if not condition: raise PipelineError(code)


def digest(raw): return hashlib.sha256(raw).hexdigest()


def _restore_error_code(error):
    """Preserve known lifecycle codes without copying arbitrary exception text."""
    if not isinstance(error, Exception): return 'LAKE_PIPELINE_INTERRUPTED'
    if len(error.args) == 1 and type(error.args[0]) is str:
        code = error.args[0]
        if type(error) is R.RuntimeError and code in RESTORE_RUNTIME_CODES: return code
        if type(error) is PipelineError and code in ('LAKE_MLFLOW_IDENTITY_UNKNOWN', 'LAKE_TRACKER_CLEANUP_UNKNOWN'):
            return code
    return 'LAKE_MLFLOW_RESTORE_FAILED'


def sources():
    from tools.local_experiment.query_read.run import sources as reader_sources
    result = reader_sources()
    paths = [*Path(__file__).parent.glob('*.py')]
    paths += [ROOT/'tools/local_experiment'/name for name in (
        'mlflow/lake_training_workload.py', 'mlflow/training_workload.py', 'mlflow/train.py',
        'mlflow/import_evaluations.py', 'mlflow/runtime.py', 'mlflow/backup.py', 'mlflow/restore.py',
        'mlflow/pyproject.toml', 'mlflow/uv.lock', 'notebook/client.py', 'notebook/selection.py',
        'notebook_assets/run.py', 'iceberg_refs/run.py', 'iceberg_sandbox/run.py')]
    paths += [ROOT/'go.mod', ROOT/'go.sum']
    result.update({str(path.relative_to(ROOT)):digest(path.read_bytes()) for path in paths})
    return result


@contextmanager
def reader_environment(output, binary):
    previous = {key:os.environ.get(key) for key in ENV_KEYS}
    original = None
    try:
        for key in ENV_KEYS: os.environ.pop(key, None)
        os.environ.update(SHIROKUMA_NOTEBOOK_RUN_DIR=str(output), SHIROKUMA_NOTEBOOK_BINARY=str(binary))
        yield
    except BaseException as error: original = error
    finally:
        for key, value in previous.items():
            try:
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
            except BaseException as error: original = A._keep(original, error)
    if original is not None: raise original


def reader_closed(output, request):
    names = ('reader-intent.json', 'reader-status.json', 'reader.stdout.jsonl')
    if not any((output/name).exists() or (output/name).is_symlink() for name in names): return True
    try:
        intent = N._parse(N._read(output/names[0], 16384))
        require(type(intent) is dict and type(intent.get('schema_version')) is int
                and intent == {'schema_version':1, 'command':'query select', 'request':request})
        return A.reader_closed(output, request=request)
    except Exception: return False


def reader_result(output, request):
    require(reader_closed(output, request), 'LAKE_READER_CLEANUP_UNKNOWN')
    status = N._parse(N._read(output/'reader-status.json', 16384))
    require(status['state'] == 'passed' and type(status['exit_code']) is int and status['exit_code'] == 0)
    token, public, closed = N._reader_frames(N._read(output/'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES*2+2),
                                            status['exit_code'], request=request)
    require(public is not None and closed is True and token == status['evidence_token'])
    return token, public


def tracker_closed(output):
    if not (output/'tracker-owner-intent.json').exists(): return True
    try:
        value = N._parse(N._read(output/'tracker-status.json', 16384))
        return (type(value) is dict and type(value.get('schema_version')) is int and value['schema_version'] == 1
                and value.get('child_exited') is True and value.get('group_empty') is True
                and type(value.get('exit_code')) is int)
    except Exception: return False


def run_tracker(output, token):
    """Own the SDK launcher's whole process group and preserve uncertain exits."""
    argv = [str(R.VENV_PYTHON), '-B', str(Path(T.__file__).resolve()), '--source-token', token]
    N._write(output/'tracker-owner-intent.json', {'schema_version':1, 'source_token':token, 'argv':argv})
    status = dict(schema_version=1, pid=None, child_exited=False, group_empty=False, exit_code=None)
    child, original, descriptors = None, None, []
    try:
        N._write(output/'tracker-status.json', status, replace=True)
        stdout = os.open(output/'tracker.stdout.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        descriptors.append(stdout)
        stderr = os.open(output/'tracker.stderr.log', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        descriptors.append(stderr)
        child = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            start_new_session=True, env={key:value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL')}, umask=0o077)
        status['pid'] = child.pid
        N._write(output/'tracker-status.json', status, replace=True)
        child.wait(timeout=TRACKER_SECONDS)
    except BaseException as error:
        original = error
        try:
            if child is not None and child.poll() is None:
                try: os.killpg(child.pid, signal.SIGINT)
                except BaseException as other: original = A._keep(original, other)
                until = time.monotonic()+TRACKER_GRACE_SECONDS
                while time.monotonic() < until and child.poll() is None:
                    try: child.wait(timeout=min(1, max(.001, until-time.monotonic())))
                    except subprocess.TimeoutExpired: pass
                    except BaseException as other: original = A._keep(original, other)
        except BaseException as other: original = A._keep(original, other)
    finally:
        for descriptor in reversed(descriptors):
            try: os.close(descriptor)
            except BaseException as error: original = A._keep(original, error)
        try:
            if child is None:
                status.update(child_exited=True, group_empty=True, exit_code=1)
            else:
                status.update(exit_code=child.poll(), child_exited=child.poll() is not None)
                status['group_empty'] = not any(row['pgid'] == child.pid for row in R.processes().values())
            N._write(output/'tracker-status.json', status, replace=True)
        except BaseException as error: original = A._keep(original, error)
    if original is not None: raise original
    require(tracker_closed(output) and status['exit_code'] == 0, 'LAKE_TRACKER_FAILED')
    return N._parse(N._read(output/'tracker.stdout.json', 16384))


def verify_tracking(value, token, store_id, output):
    require(type(value) is dict and set(value) == {'result','source_token','run_id','evidence','failure_code'}
            and value['result'] == 'verified' and value['source_token'] == token and value['failure_code'] is None)
    require(type(value['run_id']) is str and re.fullmatch(r'[0-9a-f]{32}', value['run_id']))
    prefix = 'tmp/local-experiment/mlflow-tracking/lake-training/'
    require(type(value['evidence']) is str and re.fullmatch(re.escape(prefix)+r'[0-9a-f]{32}', value['evidence']))
    attempt = value['evidence'][len(prefix):]
    directory = R.STATE/'lake-training'/attempt
    receipt = N._parse(T.E.read_private(directory/'result.json'))
    require(type(receipt) is dict and set(receipt) == {'schema_version','result','phase','token','store_id',
            'source_sha256','experiment_id','run_id','started_ms','training_finished_ms','finished_ms',
            'workload_sha256','failure_code'} and type(receipt['schema_version']) is int and receipt['schema_version'] == 1
            and receipt['result'] == receipt['phase'] == 'verified' and receipt['failure_code'] is None
            and receipt['token'] == attempt and receipt['store_id'] == store_id and receipt['run_id'] == value['run_id']
            and receipt['source_sha256'] == T.source_hashes())
    require(type(receipt['experiment_id']) is str and re.fullmatch(r'[0-9]{1,20}', receipt['experiment_id'])
            and all(type(receipt[key]) is int and 0 < receipt[key] <= 2**53 for key in
                    ('started_ms','training_finished_ms','finished_ms'))
            and receipt['started_ms'] <= receipt['training_finished_ms'] <= receipt['finished_ms'])
    raw = T.E.read_private(directory/'workload.json'); workload = N._parse(raw)
    L.validate_result(workload)
    require(raw == T.E.canonical(workload) and digest(raw) == receipt['workload_sha256']
            and T.E.canonical(workload['lake_input']) == T.E.read_private(output/'lake-input.json'))
    require(T.E.read_private(R.ARTIFACT_ROOT/'iceberg-training'/value['run_id']/'artifacts/safe-projection.json') == raw)
    return dict(attempt_token=attempt, run_id=value['run_id'], experiment_id=receipt['experiment_id'],
                workload_sha256=receipt['workload_sha256'])


def _runtime_status(runtime, store_id=None):
    value = runtime.status()
    require(type(value) is dict and value.get('state') in ('running','stopped')
            and type(value.get('store_id')) is str and str(uuid.UUID(value['store_id'])) == value['store_id']
            and (store_id is None or value['store_id'] == store_id)
            and (value['state'] != 'running' or (value.get('healthy') is True and type(value.get('identity')) is dict)),
            'LAKE_MLFLOW_IDENTITY_UNKNOWN')
    return value


def execute(output, binary, token, *, fixture_runner=F.run, read_select=N.read_select, runtime=R,
            backup_export=B.export_stopped, tracker=run_tracker, tracking_validator=verify_tracking,
            source_factory=sources, source_snapshot=None, source_validator=None):
    """Offline-testable orchestration; unknown ownership never grants a retry."""
    F.table_name(token)
    output, binary = Path(output), Path(binary)
    A._private_directory(output)
    require(output.name == token and binary.is_absolute() and binary.is_file() and not binary.is_symlink())
    code = source_factory() if source_snapshot is None else source_snapshot
    binary_sha = digest(binary.read_bytes())
    request = dict(command='select', table='local_experiment.training_'+token,
                   columns=['row_id','x1','x2','x3','target'], limit=100)
    N._write(output/'pipeline-intent.json', {'schema_version':1, 'profile':PROFILE, 'source_token':token,
                                           'source_sha256':code, 'binary_sha256':binary_sha})
    reader = output/'reader'; reader.mkdir(mode=0o700)
    report = dict(schema_version=1, profile=PROFILE, source_token=token, result='running', phase='allocated',
        source_sha256=code, binary_sha256=binary_sha, query_evidence_token=None, fixture_result=None, tracking=None,
        store_id=None, runtime_before=None, runtime_after=None, backup=None, reader_cleanup_confirmed=True,
        fixture_cleanup_confirmed=True, tracker_cleanup_confirmed=True, runtime_restored=True, error_code=None,
        failure_phase=None, runtime_restore_phase=None, runtime_restore_error_code=None)
    original, fixture_started, tracker_started, stop_attempted, source = None, False, False, False, None
    before = None

    def guard():
        require(source_factory() == code and not binary.is_symlink() and digest(binary.read_bytes()) == binary_sha,
                'LAKE_PIPELINE_SOURCE_CHANGED')

    def save(): N._write(output/'summary.json', report, replace=True)

    def phase(name, call):
        report['phase'] = name; save(); error = None; value = None
        try:
            guard(); value = call()
        except BaseException as caught: error = caught
        finally:
            try: guard()
            except BaseException as caught: error = A._keep(error, caught)
        if error is not None: raise error
        return value

    def selected(binding):
        require(type(binding) is dict and set(binding) == {'profile','fixture_token','request','identity','expected_rows'}
                and binding['profile'] == L.FIXTURE_PROFILE and binding['fixture_token'] == token
                and binding['request'] == request and type(binding['expected_rows']) is int and binding['expected_rows'] == 64)
        identity = N.C.validate_identity(binding['identity'], request, F.COLUMNS)
        with reader_environment(reader, binary):
            public = phase('reader', lambda:read_select(deepcopy(request)))
        evidence_token, saved = reader_result(reader, request)
        require(N._canonical(public) == N._canonical(saved)
                and public['table_uuid'] == identity['table_uuid'] and public['schema_sha256'] == identity['schema_sha256']
                and public['snapshot_id'] == identity['current_snapshot_id'], 'LAKE_READER_IDENTITY_CHANGED')
        report['query_evidence_token'] = evidence_token
        return L.normalize_input(public, evidence_token=evidence_token)

    try:
        save(); guard()
        fixture_started = True
        fixture_report, lake_input = phase('fixture', lambda:fixture_runner(output/'fixture', token, selected,
                                                reader_closed=lambda:reader_closed(reader, request)))
        report['fixture_result'] = fixture_report.get('result')
        require(fixture_report.get('result') == 'passed' and lake_input is not None
                and fixture_report.get('reader_cleanup_verified') is True
                and fixture_report.get('cleanup',{}).get('verified') is True
                and fixture_report.get('query_drain',{}).get('verified') is True
                and fixture_report.get('fixture_unchanged') is True and fixture_report.get('table_inventory_unchanged') is True,
                'LAKE_FIXTURE_NOT_PASSED')
        require(N._parse(N._read(output/'fixture/report.json', 256*1024)) == fixture_report)
        N._write(output/'lake-input.json', lake_input)
        ready = dict(schema_version=1, result='passed', source_token=token, fixture_profile=L.FIXTURE_PROFILE,
            query_evidence_token=lake_input['query_evidence_token'], lake_input_sha256=digest(N._read(output/'lake-input.json', L.MAX_RESULT_BYTES)),
            files={name:digest(N._read(output/name, 256*1024)) for name in sorted(T.FILES)})
        N._write(output/'source-ready.json', ready)
        source = phase('seal-source', lambda:(source_validator(token) if source_validator is not None else T.Source(token)))
        before = phase('runtime-before', lambda:_runtime_status(runtime))
        report.update(runtime_before=deepcopy(before), store_id=before['store_id'])
        if before['state'] == 'running':
            stop_attempted = True; report['runtime_restored'] = False
            phase('stop', runtime.stop)
        phase('stopped', lambda:require(runtime.require_stopped()['store_id'] == before['store_id'], 'LAKE_MLFLOW_IDENTITY_UNKNOWN'))
        backup = phase('backup', lambda:backup_export(output/'cold-backup'))
        require(type(backup) is dict and backup.get('result') == 'verified' and backup.get('store_id') == before['store_id'],
                'LAKE_BACKUP_UNVERIFIED')
        report['backup'] = backup
        phase('source-before-tracking', source.guard)
        tracker_started = True
        tracked = phase('tracking', lambda:tracker(output, token))
        require(tracker_closed(output), 'LAKE_TRACKER_CLEANUP_UNKNOWN')
        phase('source-after-tracking', source.guard)
        report['tracking'] = phase('verify-tracking', lambda:tracking_validator(tracked, token, before['store_id'], output))
    except BaseException as error:
        original = error
        report['failure_phase'] = report['phase']
    finally:
        try: report['reader_cleanup_confirmed'] = reader_closed(reader, request)
        except BaseException as error:
            report['reader_cleanup_confirmed'] = False; original = A._keep(original, error)
        if fixture_started:
            try:
                saved = N._parse(N._read(output/'fixture/report.json', 256*1024))
                report['fixture_result'] = saved['result']
                report['fixture_cleanup_confirmed'] = (saved.get('cleanup',{}).get('verified') is True
                    and saved.get('reader_cleanup_verified') is True and saved.get('query_drain',{}).get('verified') is True
                    and bool(saved.get('runtime')) and all(item.get('cleanup_verified') is True for item in saved['runtime'].values()))
            except BaseException as error:
                report['fixture_cleanup_confirmed'] = False; original = A._keep(original, error)
        if tracker_started:
            try: report['tracker_cleanup_confirmed'] = tracker_closed(output)
            except BaseException as error:
                report['tracker_cleanup_confirmed'] = False; original = A._keep(original, error)
        if before is not None:
            restore_phase = 'status-before'
            try:
                current = _runtime_status(runtime, before['store_id'])
                if stop_attempted and current['state'] == 'stopped':
                    require(report['tracker_cleanup_confirmed'], 'LAKE_TRACKER_CLEANUP_UNKNOWN')
                    restore_phase = 'start'
                    runtime.start()
                    restore_phase = 'status-after'
                    current = _runtime_status(runtime, before['store_id'])
                report['runtime_after'] = current
                report['runtime_restored'] = current['state'] == before['state']
            except BaseException as error:
                report.update(runtime_restore_phase=restore_phase, runtime_restore_error_code=_restore_error_code(error))
                report['runtime_restored'] = False; original = A._keep(original, error)
                if report['failure_phase'] is None: report['failure_phase'] = 'restore-runtime'
        try: guard()
        except BaseException as error:
            original = A._keep(original, error)
            if report['failure_phase'] is None: report['failure_phase'] = 'final-source-check'
        closed = all(report[key] for key in ('reader_cleanup_confirmed','fixture_cleanup_confirmed',
                                            'tracker_cleanup_confirmed','runtime_restored'))
        passed = original is None and closed and report['tracking'] is not None
        if not passed and report['failure_phase'] is None: report['failure_phase'] = 'final-cleanup-check'
        report.update(result='passed' if passed else 'unknown' if not closed else
                      'interrupted' if original is not None and not isinstance(original,Exception) else 'failed',
                      error_code=None if passed else 'LAKE_PIPELINE_INTERRUPTED' if original is not None and not isinstance(original,Exception)
                      else 'LAKE_PIPELINE_FAILED', phase='complete')
        try: save()
        except BaseException as error:
            original = A._keep(original, error)
            report.update(result='unknown' if not closed else 'interrupted' if not isinstance(original,Exception) else 'failed',
                          error_code='LAKE_PIPELINE_EVIDENCE_FAILED')
            if report['failure_phase'] is None: report['failure_phase'] = 'write-summary'
    if original is not None and not isinstance(original, Exception): raise original
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(json.dumps({'result':'failed','error_code':'LAKE_PIPELINE_ARGUMENTS_INVALID','evidence':None})); return 1
    os.umask(0o077)
    output, report, original = None, None, None
    try:
        with A._signals():
            code = sources()
            STATE.mkdir(parents=True, exist_ok=True, mode=0o700); A._private_directory(STATE)
            output = STATE/uuid.uuid4().hex; output.mkdir(mode=0o700)
            binary = output/'shirokuma'
            with (output/'build.log').open('xb') as log:
                subprocess.run(['go','build','-o',str(binary),'./cmd/shirokuma'], cwd=ROOT,
                               stdout=log, stderr=log, check=True, timeout=60)
            require(sources() == code, 'LAKE_PIPELINE_SOURCE_CHANGED')
            report = execute(output, binary, output.name, source_snapshot=code)
    except BaseException as error:
        original = error
        if output is not None:
            try: report = N._parse(N._read(output/'summary.json', 256*1024))
            except BaseException as other: original = A._keep(original, other)
    closed = report is None and (output is None or not (output/'pipeline-intent.json').exists())
    terminal = (type(report) is dict and report.get('phase') == 'complete'
                and report.get('result') in ('passed','failed','interrupted','unknown'))
    if report is not None:
        # A failed final save can leave the initial optimistic cleanup flags.
        # Only a terminal summary records the completed finally checks.
        closed = terminal and all(report.get(key) is True for key in ('reader_cleanup_confirmed','fixture_cleanup_confirmed',
                                                                      'tracker_cleanup_confirmed','runtime_restored'))
    result = report['result'] if terminal else 'unknown' if report is not None else 'failed'
    if original is not None:
        result = 'unknown' if not closed else 'interrupted' if not isinstance(original,Exception) else 'failed'
    owner = dict(schema_version=1, profile=PROFILE, result=result, summary_result=report.get('result') if type(report) is dict else None,
                 cleanup_confirmed=closed, error_code=None if result == 'passed' else 'LAKE_PIPELINE_FAILED')
    saved = False
    if output is not None:
        try: N._write(output/'owner-result.json', owner); saved = True
        except BaseException as error:
            original = A._keep(original,error); result = 'unknown' if not closed else 'interrupted' if not isinstance(original,Exception) else 'failed'
    print(json.dumps(dict(result=result, cleanup_confirmed=closed,
        evidence=f'tmp/local-experiment/lake-training/{output.name}/owner-result.json' if saved else None)))
    if original is not None and not isinstance(original,Exception):
        return 128+original.signum if isinstance(original,A.AssetsInterrupted) else 130
    return 0 if result == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
