"""Run the fixed Iceberg training pipeline once from the local Lab.

The parent owns the Lab lock until pipeline cleanup and worker exit are known.
The existing pipeline owns the disposable table, Reader, backup and MLflow run.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import client as N, train as Tn, training as T

LAB_STATE = ROOT / 'tmp/local-experiment/notebook-lab'
STATE = LAB_STATE / 'trainings'
PYTHON = ROOT / 'tools/local_experiment/mlflow/.venv/bin/python'
KUBECONFIG = Path.home() / '.kube/config'
PROFILE = 'interactive-fixed-iceberg-training-v1'
CACHE_NAME = 'query-cache'
MAX_ATTEMPTS = 128
ERROR_CODES = {'LAB_TRAINING_INVALID', 'LAB_TRAINING_FAILED', 'LAB_TRAINING_BUSY',
               'LAB_TRAINING_INTERRUPTED', 'LAB_TRAINING_CLEANUP_UNKNOWN'}


def require(condition, code='LAB_TRAINING_INVALID'):
    N._require(condition, code)


def _private(path):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)


def _directory(value):
    directory = Path(value)
    require(directory.is_absolute() and directory.parent == STATE and N.TOKEN.fullmatch(directory.name))
    _private(STATE)
    _private(directory)
    return directory


def _json(path, maximum=16384):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    return N._parse(N._read(path, maximum))


def arguments(directory):
    return [str(PYTHON), '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)]


def _input():
    return dict(schema_version=1, profile=PROFILE)


def _intent(directory):
    return dict(schema_version=1, profile=PROFILE, argv=arguments(directory),
                request_sha256=hashlib.sha256(N._canonical(_input())).hexdigest())


def _bound(directory):
    require(N._read(directory / 'input.json', 1024) == N._canonical(_input())
            and _json(directory / 'intent.json') == _intent(directory))


def training_status(directory):
    directory = _directory(directory)
    path = directory / 'status.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _json(path)
    require(type(value) is dict and set(value) == Tn.STATUS_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] in ('pending', 'running', 'passed', 'failed', 'interrupted', 'unknown')
            and all(type(value[k]) is bool for k in ('child_exited', 'cleanup_verified'))
            and all(value[k] is None or type(value[k]) is int and value[k] > 1 for k in ('pid', 'pgid'))
            and value['pid'] == value['pgid']
            and (value['process_start'] is None or type(value['process_start']) is str
                 and 1 <= len(value['process_start']) <= 64 and '\n' not in value['process_start'])
            and (value['evidence_token'] is None or type(value['evidence_token']) is str
                 and N.TOKEN.fullmatch(value['evidence_token']))
            and (value['exit_code'] is None or type(value['exit_code']) is int)
            and value['child_exited'] == (value['exit_code'] is not None)
            and (value['error_code'] is None or value['error_code'] in ERROR_CODES))
    require(not value['cleanup_verified'] or value['child_exited']
            and (value['exit_code'] in (0, 1, 130, 143)
                 or value['evidence_token'] is None and value['exit_code'] in (-signal.SIGINT, -signal.SIGTERM))
            and value['state'] in ('passed', 'failed', 'interrupted'))
    require(value['state'] != 'passed' or value['cleanup_verified'] and value['exit_code'] == 0
            and value['evidence_token'] is not None and value['error_code'] is None)
    return value


def _launched(directory):
    path = directory / 'launch.json'
    return path.exists() or path.is_symlink()


def terminal_receipt(directory, exit_code):
    directory = _directory(directory)
    _bound(directory)
    return Tn._terminal(directory, exit_code)


def training_closed(directory):
    directory = _directory(directory)
    value = training_status(directory)
    if (directory / 'recovery.json').exists() or (directory / 'recovery.json').is_symlink():
        _saved_recovery(directory)
        return True
    if value is None or not value['child_exited'] or not value['cleanup_verified']:
        return False
    _bound(directory)
    if value['pid'] is not None and not Tn.group_empty(value['pid']):
        return False
    if value['evidence_token'] is None:
        return not _launched(directory)
    token, _, closed, result = terminal_receipt(directory, value['exit_code'])
    return closed and token == value['evidence_token'] and (value['state'] != 'passed' or result == 'passed')


def _idle(except_token=None):
    require(all(not p.is_symlink() for p in (STATE, *STATE.parents)))
    if not STATE.exists():
        return True
    _private(STATE)
    entries = list(STATE.iterdir())
    require(len(entries) <= MAX_ATTEMPTS)
    for directory in entries:
        require(N.TOKEN.fullmatch(directory.name))
        if directory.name == except_token:
            continue
        if not training_closed(directory):
            return False
    return True


def check_idle():
    """Read-only residual check; caller holds the shared Lab owner lock."""
    return _idle()


@contextmanager
def _lock(*, recovery_token=None):
    # The ML worker imports this module without a Jupyter Server dependency.
    from tools.local_experiment.notebook_lab import runtime as R
    first = None
    try:
        with R._lock():
            try:
                require(R._tunings_idle(), 'LAB_TRAINING_CLEANUP_UNKNOWN')
                require(R._recordings_idle() and R._queries_idle() and R._comparisons_idle() and _idle(recovery_token),
                        'LAB_TRAINING_CLEANUP_UNKNOWN')
                yield
            except BaseException as error:
                first = error
                raise
    except BaseException as error:
        first = Tn._keep(first, error)
        if not isinstance(first, Exception) or isinstance(first, N.NotebookError):
            raise first
        code = 'LAB_TRAINING_BUSY' if isinstance(first, R.LabError) and str(first) == 'LAB_BUSY' else 'LAB_TRAINING_FAILED'
        raise N.NotebookError(code) from None


def environment(directory):
    cache = LAB_STATE / CACHE_NAME
    return {'PATH': str(PYTHON.parent) + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
            'LC_ALL': 'C', 'TMPDIR': str(directory / 'tmp'), 'KUBECONFIG': str(KUBECONFIG),
            'GOENV': 'off', 'GOTOOLCHAIN': 'local', 'GOCACHE': str(cache / 'build'),
            'GOPATH': str(cache / 'gopath'), 'GOMODCACHE': str(cache / 'modules'),
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
            'DO_NOT_TRACK': 'true', 'MLFLOW_DISABLE_TELEMETRY': 'true'}


@contextmanager
def _outputs(directory):
    streams, first = [], None
    try:
        for name in ('pipeline.stdout.json', 'pipeline.stderr.log'):
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            streams.append(os.fdopen(fd, 'wb'))
        yield streams
    except BaseException as error:
        first = error
    finally:
        for stream in reversed(streams):
            try:
                stream.close()
            except BaseException as error:
                first = Tn._keep(first, error)
    if first is not None:
        raise first


def _review(token, summary):
    require(type(summary) is dict)
    tracking = summary.get('tracking')
    require(type(tracking) is dict and set(tracking) == {'attempt_token', 'run_id', 'experiment_id', 'workload_sha256'})
    public = T.load_saved_review(tracking['attempt_token'], kind='iceberg')
    T.validate_public(public)
    require(all(public['identity'][key] == tracking[key] for key in tracking)
            and public['identity']['store_id'] == summary.get('store_id')
            and public['lake_source']['source_token'] == token
            and public['lake_source']['query_evidence_token'] == summary.get('query_evidence_token'))
    return public


def _recovery_files(directory, token):
    output = Tn.LAKE_STATE / token
    names = {name: directory / name for name in
             ('input.json', 'intent.json', 'launch.json', 'status.json', 'pipeline.stdout.json')}
    names.update({'pipeline/' + name: output / name for name in
                  ('owner-result.json', 'summary.json', 'tracker-owner-intent.json', 'tracker-status.json',
                   'reader/reader-intent.json', 'reader/reader-status.json', 'reader/reader.stdout.jsonl')})
    result = {}
    for name, path in names.items():
        require(all(not p.is_symlink() for p in (path, *path.parents)))
        raw = N._read(path, max(256 * 1024, N.C.MAX_PUBLIC_BYTES * 2 + 2))
        result[name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return result


def _restored_runtime(value, store_id):
    from tools.local_experiment.mlflow import runtime as M
    require(type(value) is dict and set(value) == {'schema_version', 'state', 'store_id', 'identity', 'healthy'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] == 'running' and value['healthy'] is True and value['store_id'] == store_id)
    identity = value['identity']
    require(type(identity) is dict and set(identity) == {'pid', 'pgid', 'start_time', 'argv'}
            and type(identity['pid']) is int and identity['pid'] > 1 and type(identity['pgid']) is int
            and identity['pid'] == identity['pgid'] and type(identity['start_time']) is str
            and 1 <= len(identity['start_time']) <= 64 and '\n' not in identity['start_time']
            and identity['argv'] == M.server_argv())


def _recovery_base(directory):
    """Only a finished training whose sole failure was server restoration."""
    from tools.local_experiment.mlflow import runtime as M
    from tools.local_experiment.notebook_assets import run as A
    directory = _directory(directory)
    status = training_status(directory)
    require(status is not None and status['state'] == 'unknown' and status['child_exited'] is True
            and status['exit_code'] == 1 and status['cleanup_verified'] is False
            and status['error_code'] == 'LAB_TRAINING_CLEANUP_UNKNOWN'
            and status['evidence_token'] is not None and status['pid'] is not None)
    token = status['evidence_token']
    before = _recovery_files(directory, token)
    source_token, summary, closed, result = terminal_receipt(directory, status['exit_code'])
    require(source_token == token and closed is False and result == 'unknown' and type(summary) is dict
            and summary.get('result') == 'unknown' and summary.get('failure_phase') == 'restore-runtime'
            and summary.get('error_code') == 'LAKE_PIPELINE_FAILED' and summary.get('fixture_result') == 'passed'
            and summary.get('runtime_restored') is False
            and all(summary.get(k) is True for k in
                    ('reader_cleanup_confirmed', 'fixture_cleanup_confirmed', 'tracker_cleanup_confirmed')))
    launch = _json(directory / 'launch.json')
    require(type(launch) is dict and set(launch) == {'pid', 'ppid', 'pgid', 'process_start'}
            and launch['pid'] == status['pid'] and launch['pgid'] == status['pgid']
            and launch['process_start'] == status['process_start']
            and type(launch['ppid']) is int and launch['ppid'] > 1)
    output = Tn.LAKE_STATE / token
    request = dict(command='select', table='local_experiment.training_' + token,
                   columns=['row_id', 'x1', 'x2', 'x3', 'target'], limit=100)
    intent = _json(output / 'reader/reader-intent.json')
    require(type(intent) is dict and type(intent.get('schema_version')) is int
            and intent == dict(schema_version=1, command='query select', request=request)
            and A.reader_closed(output / 'reader', request=request))
    reader = _json(output / 'reader/reader-status.json')
    require(reader['state'] == 'passed' and reader['evidence_token'] == summary.get('query_evidence_token'))
    tracker = _json(output / 'tracker-status.json')
    require(type(tracker) is dict and set(tracker) == {'schema_version', 'pid', 'child_exited', 'group_empty', 'exit_code'}
            and type(tracker['schema_version']) is int and tracker['schema_version'] == 1
            and type(tracker['pid']) is int and tracker['pid'] > 1 and type(tracker['exit_code']) is int
            and tracker['exit_code'] == 0 and tracker['child_exited'] is True and tracker['group_empty'] is True)
    require(_json(output / 'tracker-owner-intent.json') == dict(schema_version=1, source_token=token,
            argv=[str(M.VENV_PYTHON), '-B', str(ROOT / 'tools/local_experiment/lake_training/tracking.py'),
                  '--source-token', token]))
    require(all(Tn.group_empty(pid) for pid in (status['pid'], reader['pid'], tracker['pid'])))
    public = _review(token, summary)
    _, observation, reader_closed = N._reader_frames(
        N._read(output / 'reader/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request=request)
    require(reader_closed is True and observation is not None
            and all(observation[key] == public['lake_source'][key] for key in
                    ('table', 'table_uuid', 'snapshot_id', 'schema_sha256', 'rows_sha256')))
    marker = M.require_store()
    require(marker['store_id'] == public['identity']['store_id'])
    _restored_runtime(summary.get('runtime_before'), marker['store_id'])
    require(_recovery_files(directory, token) == before)
    return dict(schema_version=1, profile='interactive-training-recovery-v1', attempt_token=directory.name,
                source_token=token, evidence=before, store_marker_sha256=hashlib.sha256(N._canonical(marker)).hexdigest(),
                review_sha256=hashlib.sha256(N._canonical(public)).hexdigest(), review=public)


def _saved_recovery(directory):
    """The recorded restoration remains closed across later normal server stops."""
    record = _json(directory / 'recovery.json', 256 * 1024)
    base = _recovery_base(directory)
    require(type(record) is dict and set(record) == set(base) | {'restored_runtime'}
            and all(record[key] == value for key, value in base.items()))
    _restored_runtime(record['restored_runtime'], base['review']['identity']['store_id'])
    return base['review']


def recover_training(attempt_token):
    """Recover an already trained result after an independently repaired runtime.

No fit, process signalling or runtime mutation. Original receipts remain intact;
only a new immutable recovery.json records the verified restoration.
"""
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token))
        with _lock(recovery_token=attempt_token):
            directory = _directory(STATE / attempt_token)
            if (directory / 'recovery.json').exists() or (directory / 'recovery.json').is_symlink():
                return _saved_recovery(directory)
            from tools.local_experiment.mlflow import runtime as M
            base = _recovery_base(directory)
            current = M.status()
            _restored_runtime(current, base['review']['identity']['store_id'])
            require(_recovery_base(directory) == base)
            N._write(directory / 'recovery.json', dict(**base, restored_runtime=current))
            return _saved_recovery(directory)
    except Exception:
        raise N.NotebookError('LAB_TRAINING_RECOVERY_INVALID') from None


def train_lake():
    """Create one fixed training run and return its validated saved review."""
    with _lock():
        for path in (STATE, LAB_STATE / CACHE_NAME, *(LAB_STATE / CACHE_NAME / name
                     for name in ('build', 'gopath', 'modules'))):
            if not path.exists():
                path.mkdir(mode=0o700)
            _private(path)
        require(len(list(STATE.iterdir())) < MAX_ATTEMPTS, 'LAB_TRAINING_BUSY')
        directory = STATE / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        (directory / 'tmp').mkdir(mode=0o700)
        N._write(directory / 'input.json', _input())
        N._write(directory / 'intent.json', _intent(directory))
        status = dict(schema_version=1, state='pending', pid=None, pgid=None, process_start=None,
                      evidence_token=None, exit_code=None, child_exited=False, cleanup_verified=False, error_code=None)
        child, first, public, interrupted = None, None, None, False
        try:
            N._write(directory / 'status.json', status, replace=True)
            with _outputs(directory) as (stdout, stderr):
                child = subprocess.Popen(arguments(directory), cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, env=environment(directory), start_new_session=True, umask=0o077)
                status.update(state='running', pid=child.pid, pgid=child.pid)
                N._write(directory / 'status.json', status, replace=True)
                status['process_start'] = N._process_start(child.pid)
                N._write(directory / 'status.json', status, replace=True)
                N._write(directory / 'launch.json', dict(pid=child.pid, ppid=os.getpid(), pgid=child.pid,
                         process_start=status['process_start']))
                try:
                    child.wait(timeout=Tn.TRAIN_SECONDS)
                except BaseException as error:
                    first, interrupted = error, True
                    first = Tn._interrupt(child, first)
        except BaseException as error:
            first = Tn._keep(first, error)
            try:
                if child is not None and child.poll() is None and not interrupted:
                    first = Tn._interrupt(child, first)
            except BaseException as error:
                first = Tn._keep(first, error)
        finally:
            try:
                if child is None:
                    status.update(exit_code=1, child_exited=True, cleanup_verified=True)
                else:
                    code = child.poll()
                    status.update(exit_code=code, child_exited=code is not None)
                    require(code is not None and Tn.group_empty(child.pid), 'LAB_TRAINING_CLEANUP_UNKNOWN')
                    if not _launched(directory):
                        status['cleanup_verified'] = True
                        require(False, 'LAB_TRAINING_FAILED')
                    token, summary, closed, result = terminal_receipt(directory, code)
                    status.update(evidence_token=token, cleanup_verified=closed)
                    require(closed, 'LAB_TRAINING_CLEANUP_UNKNOWN')
                    require(result == 'passed', 'LAB_TRAINING_FAILED')
                    if first is None:
                        public = _review(token, summary)
                        N._write(directory / 'training-review.json', public)
            except BaseException as error:
                first = Tn._keep(first, error)
            status.update(state='passed' if first is None else 'unknown' if not status['cleanup_verified'] else
                          'interrupted' if not isinstance(first, Exception) else 'failed',
                          error_code=None if first is None else 'LAB_TRAINING_INTERRUPTED' if not isinstance(first, Exception)
                          else 'LAB_TRAINING_CLEANUP_UNKNOWN' if not status['cleanup_verified'] else 'LAB_TRAINING_FAILED')
            try:
                N._write(directory / 'status.json', status, replace=True)
            except BaseException as error:
                first = Tn._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception):
                raise first
            raise N.NotebookError(status['error_code'] or 'LAB_TRAINING_FAILED') from None
        return public


def worker(directory, *, pipeline=None):
    directory = _directory(directory)
    _bound(directory)
    deadline = time.monotonic() + Tn.LAUNCH_SECONDS
    while not _launched(directory):
        require(time.monotonic() < deadline)
        time.sleep(.05)
    status = training_status(directory)
    require(status is not None and status['state'] == 'running' and status['pid'] == status['pgid'] == os.getpid()
            and os.getpgrp() == os.getpid() and status['process_start'] == N._process_start(os.getpid())
            and _json(directory / 'launch.json') == dict(pid=os.getpid(), ppid=os.getppid(), pgid=os.getpid(),
                 process_start=status['process_start']))
    if pipeline is None:
        from tools.local_experiment.lake_training import run as P
        pipeline = P.main
    return pipeline([])


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        require(type(args) is list and len(args) == 2 and args[0] == '--worker')
        return worker(args[1])
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
