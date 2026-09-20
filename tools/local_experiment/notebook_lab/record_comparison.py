"""Record an existing comparison once, with owned worker and ML runtime recovery."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import import_comparisons as I, backup as B, runtime as M
from tools.local_experiment.notebook_lab import compare as C

N, T = C.N, C.T
LAB_STATE = C.LAB_STATE
STATE = LAB_STATE / 'comparison-recordings'
PYTHON = C.PYTHON
PROFILE = 'interactive-saved-comparison-recording-v1'
SECONDS, CLEANUP_SECONDS, MAX_ATTEMPTS = 180, 120, 128
ERROR_CODES = {'LAB_RECORD_INVALID', 'LAB_RECORD_FAILED', 'LAB_RECORD_BUSY',
               'LAB_RECORD_INTERRUPTED', 'LAB_RECORD_CLEANUP_UNKNOWN'}
SCOPE = 'Import an existing comparison; no fit, Reader or model promotion. Reused receipts are saved evidence, not a fresh database readback.'
require = lambda value, code='LAB_RECORD_INVALID': N._require(value, code)
_private, _json, _outputs = C._private, C._json, C._outputs


class Interrupted(BaseException):
    def __init__(self, signum): self.signum = signum


def arguments(directory):
    return [str(PYTHON), '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)]


def _directory(value):
    directory = Path(value)
    require(directory.is_absolute() and directory.parent == STATE and N.TOKEN.fullmatch(directory.name))
    _private(STATE); _private(directory)
    return directory


def _input(directory):
    raw = N._read(directory / 'input.json', 1024)
    value = N._parse(raw)
    require(type(value) is dict and set(value) == {'schema_version', 'comparison_token'}
            and type(value['schema_version']) is int and value['schema_version'] == 1)
    I.normalize_token(value['comparison_token'])
    require(raw == N._canonical(value))
    return value


def _bound(directory):
    request = _input(directory)
    require(_json(directory / 'intent.json') == dict(schema_version=1, profile=PROFILE,
            argv=arguments(directory), request_sha256=I.digest(request)))
    return request


def recording_status(directory):
    directory = _directory(directory)
    path = directory / 'status.json'
    if not path.exists() and not path.is_symlink(): return None
    value = _json(path)
    require(type(value) is dict and set(value) == T.STATUS_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] in ('pending', 'running', 'passed', 'failed', 'interrupted', 'unknown')
            and all(type(value[k]) is bool for k in ('child_exited', 'cleanup_verified'))
            and all(value[k] is None or type(value[k]) is int and value[k] > 1 for k in ('pid', 'pgid'))
            and value['pid'] == value['pgid'] and value['evidence_token'] == directory.name
            and (value['process_start'] is None or type(value['process_start']) is str
                 and 1 <= len(value['process_start']) <= 64 and '\n' not in value['process_start'])
            and (value['exit_code'] is None or type(value['exit_code']) is int)
            and value['child_exited'] == (value['exit_code'] is not None)
            and (value['error_code'] is None or value['error_code'] in ERROR_CODES))
    require(not value['cleanup_verified'] or value['child_exited']
            and value['state'] in ('passed', 'failed', 'interrupted'))
    require(value['state'] != 'passed' or value['cleanup_verified'] and value['exit_code'] == 0
            and value['error_code'] is None)
    return value


def _summary(directory, token):
    value = _json(directory / 'worker-summary.json', 128 * 1024)
    require(type(value) is dict and set(value) == {'schema_version', 'profile', 'comparison_token',
            'result', 'failure_phase', 'error_code', 'runtime_before', 'runtime_after',
            'runtime_restored', 'backup', 'import_receipt', 'cleanup_verified'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE and value['comparison_token'] == token
            and value['result'] in ('passed', 'failed', 'interrupted', 'unknown')
            and type(value['runtime_restored']) is bool and type(value['cleanup_verified']) is bool
            and value['cleanup_verified'] == value['runtime_restored']
            and (value['error_code'] is None or value['error_code'] in ERROR_CODES)
            and (value['failure_phase'] is None or value['failure_phase'] in
                 ('source', 'runtime-before', 'stop', 'backup', 'import', 'verify', 'restore-runtime', 'source-after')))
    before, after = value['runtime_before'], value['runtime_after']
    if before is not None:
        _validate_runtime(before)
        if value['runtime_restored']:
            _validate_runtime(after, before['store_id'])
            require(before['state'] == after['state'])
    require(value['result'] != 'passed' or value['cleanup_verified'] and before is not None
            and value['failure_phase'] is None and value['error_code'] is None
            and type(value['import_receipt']) is dict and value['import_receipt'].get('result') == 'verified')
    return value


def recording_closed(directory):
    value = recording_status(directory)
    if value is None or not value['child_exited'] or not value['cleanup_verified']: return False
    request = _bound(directory)
    if value['pid'] is None: return not (directory / 'launch.json').exists()
    if not T.group_empty(value['pid']): return False
    summary = _summary(directory, request['comparison_token'])
    return summary['cleanup_verified'] and (value['state'] != 'passed' or summary['result'] == 'passed')


def check_idle():
    """Read-only residual check; caller holds the shared Lab owner lock."""
    require(all(not p.is_symlink() for p in (STATE, *STATE.parents)))
    if not STATE.exists(): return True
    _private(STATE)
    entries = list(STATE.iterdir())
    require(len(entries) <= MAX_ATTEMPTS)
    return all(recording_closed(_directory(path)) for path in entries)


@contextmanager
def _lock():
    from tools.local_experiment.notebook_lab import runtime as R
    first = None
    try:
        with R._lock():
            try:
                require(R._queries_idle() and R._comparisons_idle() and R._trainings_idle()
                        and check_idle(), 'LAB_RECORD_CLEANUP_UNKNOWN')
                yield
            except BaseException as error:
                first = error; raise
    except BaseException as error:
        first = T._keep(first, error)
        if not isinstance(first, Exception) or isinstance(first, N.NotebookError): raise first
        code = 'LAB_RECORD_BUSY' if isinstance(first, R.LabError) and str(first) == 'LAB_BUSY' else 'LAB_RECORD_FAILED'
        raise N.NotebookError(code) from None


def _public(token, receipt, *, attempt_token=None, reused=False):
    require(type(receipt) is dict and receipt.get('result') == 'verified'
            and receipt.get('comparison_token') == token and receipt.get('source_unchanged') is True)
    source = I.Snapshot(token).load()
    require(receipt['projection_sha256'] == I.digest(source)
            and receipt['store_id'] == source['comparison']['identity']['store_id'])
    return dict(schema_version=1, profile=PROFILE, result='passed', cleanup_verified=True,
                attempt_token=attempt_token, comparison_token=token, reused=reused,
                comparison=source['comparison'], import_receipt=deepcopy(receipt), scope=SCOPE)


def _interrupt(child, start, first):
    try:
        if child.poll() is None:
            require(start is not None and N._process_start(child.pid) == start)
            os.kill(child.pid, signal.SIGINT)
    except BaseException as error: first = T._keep(first, error)
    deadline = time.monotonic() + CLEANUP_SECONDS
    while child.poll() is None and time.monotonic() < deadline:
        try: child.wait(timeout=min(1, max(.001, deadline-time.monotonic())))
        except subprocess.TimeoutExpired: pass
        except BaseException as error: first = T._keep(first, error)
    return first


def record_comparison(comparison_token):
    """Record once; later calls return the validated original saved receipt."""
    try: token = I.normalize_token(comparison_token)
    except Exception: raise N.NotebookError('LAB_RECORD_INVALID') from None
    with _lock():
        try:
            previous = I.recorded(token)
            if previous is not None: return _public(token, previous, reused=True)
        except Exception: raise N.NotebookError('LAB_RECORD_FAILED') from None
        STATE.mkdir(mode=0o700, exist_ok=True); _private(STATE)
        require(len(list(STATE.iterdir())) < MAX_ATTEMPTS, 'LAB_RECORD_BUSY')
        directory = STATE / uuid.uuid4().hex; directory.mkdir(mode=0o700)
        request = dict(schema_version=1, comparison_token=token)
        N._write(directory / 'input.json', request)
        N._write(directory / 'intent.json', dict(schema_version=1, profile=PROFILE,
                 argv=arguments(directory), request_sha256=I.digest(request)))
        status = dict(schema_version=1, state='pending', pid=None, pgid=None, process_start=None,
                      evidence_token=directory.name, exit_code=None, child_exited=False, cleanup_verified=False, error_code=None)
        child, first, public, interrupted = None, None, None, False
        try:
            N._write(directory / 'status.json', status, replace=True)
            with _outputs(directory) as (stdout, stderr):
                child = subprocess.Popen(arguments(directory), cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, env=M.environment(), start_new_session=True, umask=0o077)
                status.update(state='running', pid=child.pid, pgid=child.pid)
                status['process_start'] = N._process_start(child.pid)
                row = M.processes().get(child.pid)
                require(row is not None and row['ppid'] == os.getpid() and row['pgid'] == child.pid
                        and row['uid'] == os.getuid() and row['start_time'] == status['process_start']
                        and row['command'] == ' '.join(arguments(directory)))
                N._write(directory / 'status.json', status, replace=True)
                N._write(directory / 'launch.json', dict(pid=child.pid, ppid=os.getpid(), pgid=child.pid,
                         process_start=status['process_start']))
                try: child.wait(timeout=SECONDS)
                except BaseException as error:
                    first, interrupted = error, True
                    first = _interrupt(child, status['process_start'], first)
        except BaseException as error:
            first = T._keep(first, error)
            if child is not None and child.poll() is None and not interrupted:
                first = _interrupt(child, status['process_start'], first)
        finally:
            try:
                if child is None: status.update(exit_code=1, child_exited=True, cleanup_verified=True)
                else:
                    code = child.poll(); status.update(exit_code=code, child_exited=code is not None)
                    if code is not None and T.group_empty(child.pid):
                        summary = _summary(directory, token)
                        status['cleanup_verified'] = summary['cleanup_verified']
                        if first is None:
                            require(code == 0 and summary['result'] == 'passed', 'LAB_RECORD_FAILED')
                            require(_bound(directory) == request)
                            receipt = I.recorded(token)
                            require(receipt == summary['import_receipt'])
                            public = _public(token, receipt, attempt_token=directory.name)
                require(status['cleanup_verified'], 'LAB_RECORD_CLEANUP_UNKNOWN')
                if first is None: N._write(directory / 'result.json', public)
            except BaseException as error: first = T._keep(first, error)
            status.update(state='passed' if first is None else 'unknown' if not status['cleanup_verified'] else
                          'interrupted' if not isinstance(first, Exception) else 'failed',
                          error_code=None if first is None else 'LAB_RECORD_INTERRUPTED' if not isinstance(first, Exception)
                          else 'LAB_RECORD_CLEANUP_UNKNOWN' if not status['cleanup_verified'] else 'LAB_RECORD_FAILED')
            try: N._write(directory / 'status.json', status, replace=True)
            except BaseException as error: first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception): raise first
            raise N.NotebookError(status['error_code'] or 'LAB_RECORD_FAILED') from None
        return public


def _validate_runtime(value, store_id=None):
    require(type(value) is dict and value.get('state') in ('running', 'stopped')
            and type(value.get('store_id')) is str and str(uuid.UUID(value['store_id'])) == value['store_id']
            and (store_id is None or value['store_id'] == store_id)
            and (value['state'] != 'running' or value.get('healthy') is True
                 and type(value.get('identity')) is dict), 'LAB_RECORD_CLEANUP_UNKNOWN')
    return value


def execute(directory, token, *, runtime=M, export=B.export_stopped, importer=I.worker):
    """Worker-only synchronous import; restore the same store's prior state."""
    summary = dict(schema_version=1, profile=PROFILE, comparison_token=token, result='failed',
        failure_phase=None, error_code=None, runtime_before=None, runtime_after=None,
        runtime_restored=True, backup=None, import_receipt=None, cleanup_verified=False)
    before, source, first, stopped, phase = None, None, None, False, 'source'
    try:
        source = I.Snapshot(token); source.load()
        require(I.recorded(token) is None, 'LAB_RECORD_FAILED')
        phase = 'runtime-before'; before = _validate_runtime(runtime.status())
        summary['runtime_before'] = deepcopy(before)
        if before['state'] == 'running':
            phase = 'stop'; stopped = True; summary['runtime_restored'] = False
            runtime.stop()
        require(runtime.require_stopped()['store_id'] == before['store_id'])
        phase = 'backup'; backup = export(directory / 'cold-backup')
        require(type(backup) is dict and backup.get('result') == 'verified'
                and backup.get('store_id') == before['store_id'])
        summary['backup'] = dict(store_id=before['store_id'], manifest='backup-manifest.json',
                               manifest_sha256=I.digest(backup))
        source.check(); phase = 'import'
        require(importer(token) == 0, 'LAB_RECORD_FAILED')
        phase = 'verify'; summary['import_receipt'] = I.recorded(token)
        require(summary['import_receipt'] is not None, 'LAB_RECORD_FAILED')
    except BaseException as error:
        first = error; summary['failure_phase'] = phase
    finally:
        if before is not None:
            try:
                current = _validate_runtime(runtime.status(), before['store_id'])
                if stopped and current['state'] == 'stopped':
                    runtime.start(); current = _validate_runtime(runtime.status(), before['store_id'])
                summary['runtime_after'] = current
                summary['runtime_restored'] = current['state'] == before['state']
                require(summary['runtime_restored'], 'LAB_RECORD_CLEANUP_UNKNOWN')
            except BaseException as error:
                first = T._keep(first, error); summary['runtime_restored'] = False
                if summary['failure_phase'] is None: summary['failure_phase'] = 'restore-runtime'
        if source is not None:
            try: source.check()
            except BaseException as error:
                first = T._keep(first, error)
                if summary['failure_phase'] is None: summary['failure_phase'] = 'source-after'
        summary['cleanup_verified'] = summary['runtime_restored']
        summary.update(result='passed' if first is None and summary['cleanup_verified'] else
                       'unknown' if not summary['cleanup_verified'] else
                       'interrupted' if first is not None and not isinstance(first, Exception) else 'failed',
                       error_code=None if first is None else 'LAB_RECORD_INTERRUPTED' if not isinstance(first, Exception)
                       else 'LAB_RECORD_FAILED')
        try: N._write(directory / 'worker-summary.json', summary)
        except BaseException as error: first = T._keep(first, error)
    if first is not None and not isinstance(first, Exception): raise first
    return 0 if first is None and summary['cleanup_verified'] else 1


def worker(directory):
    directory = _directory(directory); request = _bound(directory)
    deadline = time.monotonic() + 30
    while not (directory / 'launch.json').exists():
        require(time.monotonic() < deadline); time.sleep(.05)
    status = recording_status(directory)
    require(status is not None and status['state'] == 'running' and status['pid'] == status['pgid'] == os.getpid()
            and os.getpgrp() == os.getpid() and status['process_start'] == N._process_start(os.getpid())
            and _json(directory / 'launch.json') == dict(pid=os.getpid(), ppid=os.getppid(), pgid=os.getpid(),
                 process_start=status['process_start']))
    return execute(directory, request['comparison_token'])


def main(argv=None):
    previous, first, code = {}, None, 1
    def caught(signum, frame): raise Interrupted(signum)
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, caught)
        args = sys.argv[1:] if argv is None else argv
        require(type(args) is list and len(args) == 2 and args[0] == '--worker')
        code = worker(args[1])
    except BaseException as error: first = error
    finally:
        for signum, handler in previous.items():
            try: signal.signal(signum, handler)
            except BaseException as error: first = T._keep(first, error)
    if first is not None:
        return 128 + first.signum if isinstance(first, Interrupted) else 130 if not isinstance(first, Exception) else 1
    return code


if __name__ == '__main__':
    raise SystemExit(main())
