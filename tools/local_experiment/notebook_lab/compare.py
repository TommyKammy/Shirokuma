"""Run one offline comparison in the existing ML environment from JupyterLab.

The parent holds the Lab owner lock through worker exit. The worker acquires no
external resources; process exit and an empty owned group establish cleanup.
"""
from __future__ import annotations

from contextlib import contextmanager
from html import escape
import io
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
from tools.local_experiment.mlflow import compare as C
from tools.local_experiment.notebook import client as N, train as T

LAB_STATE = ROOT / 'tmp/local-experiment/notebook-lab'
STATE = LAB_STATE / 'comparisons'
PYTHON = ROOT / 'tools/local_experiment/mlflow/.venv/bin/python'
PROFILE = 'interactive-ridge-comparison-v1'
SECONDS, CLEANUP_SECONDS, MAX_ATTEMPTS = 30, 10, 128
ERROR_CODES = {'LAB_COMPARISON_INVALID', 'LAB_COMPARISON_FAILED', 'LAB_COMPARISON_BUSY',
               'LAB_COMPARISON_INTERRUPTED', 'LAB_COMPARISON_CLEANUP_UNKNOWN'}


def require(condition, code='LAB_COMPARISON_INVALID'):
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


def _input(directory):
    raw = N._read(directory / 'input.json', C.MAX_INPUT_BYTES)
    value = C.parse_request(raw)
    require(raw == N._canonical(value))
    return value


def comparison_status(directory):
    directory = _directory(directory)
    path = directory / 'status.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _json(path)
    require(type(value) is dict and set(value) == T.STATUS_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] in ('pending', 'running', 'passed', 'failed', 'interrupted', 'unknown')
            and all(type(value[k]) is bool for k in ('child_exited', 'cleanup_verified'))
            and all(value[k] is None or type(value[k]) is int and value[k] > 1 for k in ('pid', 'pgid'))
            and value['pid'] == value['pgid']
            and (value['process_start'] is None or type(value['process_start']) is str
                 and 1 <= len(value['process_start']) <= 64 and '\n' not in value['process_start'])
            and value['evidence_token'] == directory.name
            and (value['exit_code'] is None or type(value['exit_code']) is int)
            and value['child_exited'] == (value['exit_code'] is not None)
            and (value['error_code'] is None or value['error_code'] in ERROR_CODES))
    require(not value['cleanup_verified'] or value['child_exited']
            and value['state'] in ('passed', 'failed', 'interrupted'))
    require(value['state'] != 'passed' or value['cleanup_verified'] and value['exit_code'] == 0
            and value['error_code'] is None)
    return value


def comparison_closed(directory):
    """A malformed calculation result can still have a proven exited worker."""
    value = comparison_status(directory)
    return bool(value is not None and value['child_exited'] and value['cleanup_verified']
                and (value['pid'] is None or T.group_empty(value['pid'])))


def check_idle():
    """Read-only residual check; the caller also owns Lab owner.lock."""
    require(all(not p.is_symlink() for p in (STATE, *STATE.parents)))
    if not STATE.exists():
        return True
    _private(STATE)
    entries = list(STATE.iterdir())
    require(len(entries) <= MAX_ATTEMPTS)
    for directory in entries:
        require(N.TOKEN.fullmatch(directory.name))
        if not comparison_closed(directory):
            return False
    return True


@contextmanager
def _lock():
    # Lazy import: the ML worker needs neither Jupyter Server nor its runtime.
    from tools.local_experiment.notebook_lab import runtime as R
    first = None
    try:
        with R._lock():
            try:
                require(R._recordings_idle(), 'LAB_COMPARISON_CLEANUP_UNKNOWN')
                require(R._trainings_idle(), 'LAB_COMPARISON_CLEANUP_UNKNOWN')
                require(R._queries_idle(), 'LAB_COMPARISON_CLEANUP_UNKNOWN')
                require(check_idle(), 'LAB_COMPARISON_CLEANUP_UNKNOWN')
                yield
            except BaseException as error:
                first = error
                raise
    except BaseException as error:
        first = T._keep(first, error)
        if not isinstance(first, Exception) or isinstance(first, N.NotebookError):
            raise first
        code = 'LAB_COMPARISON_BUSY' if isinstance(first, R.LabError) and str(first) == 'LAB_BUSY' else 'LAB_COMPARISON_FAILED'
        raise N.NotebookError(code) from None


def environment(directory):
    return {'PATH': str(PYTHON.parent) + ':/usr/bin:/bin', 'LC_ALL': 'C', 'TMPDIR': str(directory / 'tmp'),
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
            'DO_NOT_TRACK': 'true', 'MLFLOW_DISABLE_TELEMETRY': 'true'}


def _interrupt(child, first):
    try:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGINT)
    except BaseException as error:
        first = T._keep(first, error)
    deadline = time.monotonic() + CLEANUP_SECONDS
    while time.monotonic() < deadline:
        try:
            if child.poll() is not None:
                break
            child.wait(timeout=min(1, max(.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        except BaseException as error:
            first = T._keep(first, error)
    return first


@contextmanager
def _outputs(directory):
    streams, first = [], None
    try:
        for name in ('worker.stdout.json', 'worker.stderr.log'):
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
                first = T._keep(first, error)
    if first is not None:
        raise first


def _result(directory, request, exit_code):
    require(type(exit_code) is int and exit_code == 0, 'LAB_COMPARISON_FAILED')
    raw = N._read(directory / 'worker.stdout.json', C.MAX_PUBLIC_BYTES)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1)
    require(_input(directory) == request)
    comparison = C.validate_result(N._parse(raw), request)
    return dict(schema_version=1, profile=PROFILE, result='passed', attempt_token=directory.name,
                cleanup_verified=True, comparison=comparison)


def compare(training_token, alpha=10.0):
    """Return a single candidate versus the saved alpha=1 reference."""
    try:
        request = C.normalize_request(dict(schema_version=1, training_token=training_token, alpha=alpha))
    except Exception:
        raise N.NotebookError('LAB_COMPARISON_INVALID') from None
    with _lock():
        if not STATE.exists():
            STATE.mkdir(mode=0o700)
        _private(STATE)
        require(len(list(STATE.iterdir())) < MAX_ATTEMPTS, 'LAB_COMPARISON_BUSY')
        directory = STATE / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        (directory / 'tmp').mkdir(mode=0o700)
        N._write(directory / 'input.json', request)
        N._write(directory / 'intent.json', dict(schema_version=1, profile=PROFILE,
                 argv=arguments(directory), request_sha256=C.digest(request)))
        status = dict(schema_version=1, state='pending', pid=None, pgid=None, process_start=None,
                      evidence_token=directory.name, exit_code=None, child_exited=False, cleanup_verified=False, error_code=None)
        child, first, public, interrupted = None, None, None, False
        try:
            N._write(directory / 'status.json', status, replace=True)
            with _outputs(directory) as (stdout, stderr):
                child = subprocess.Popen(arguments(directory), cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, env=environment(directory), start_new_session=True, umask=0o077)
                status.update(state='running', pid=child.pid, pgid=child.pid)
                status['process_start'] = N._process_start(child.pid)
                N._write(directory / 'status.json', status, replace=True)
                N._write(directory / 'launch.json', dict(pid=child.pid, ppid=os.getpid(), pgid=child.pid,
                         process_start=status['process_start']))
                try:
                    child.wait(timeout=SECONDS)
                except BaseException as error:
                    first, interrupted = error, True
                    first = _interrupt(child, first)
        except BaseException as error:
            first = T._keep(first, error)
            if child is not None and child.poll() is None and not interrupted:
                first = _interrupt(child, first)
        finally:
            try:
                if child is None:
                    status.update(exit_code=1, child_exited=True, cleanup_verified=True)
                else:
                    code = child.poll()
                    status.update(exit_code=code, child_exited=code is not None)
                    status['cleanup_verified'] = code is not None and T.group_empty(child.pid)
                require(status['cleanup_verified'], 'LAB_COMPARISON_CLEANUP_UNKNOWN')
                if first is None:
                    public = _result(directory, request, status['exit_code'])
                    N._write(directory / 'result.json', public)
            except BaseException as error:
                first = T._keep(first, error)
            status.update(state='passed' if first is None else 'unknown' if not status['cleanup_verified'] else
                          'interrupted' if not isinstance(first, Exception) else 'failed',
                          error_code=None if first is None else 'LAB_COMPARISON_INTERRUPTED' if not isinstance(first, Exception)
                          else 'LAB_COMPARISON_CLEANUP_UNKNOWN' if not status['cleanup_verified'] else 'LAB_COMPARISON_FAILED')
            try:
                N._write(directory / 'status.json', status, replace=True)
            except BaseException as error:
                first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception):
                raise first
            raise N.NotebookError(status['error_code'] or 'LAB_COMPARISON_FAILED') from None
        return public


def worker(directory, *, calculate=None):
    directory = _directory(directory)
    request = _input(directory)
    require(_json(directory / 'intent.json') == dict(schema_version=1, profile=PROFILE,
            argv=arguments(directory), request_sha256=C.digest(request)))
    deadline = time.monotonic() + 30
    while not (directory / 'launch.json').exists():
        require(time.monotonic() < deadline)
        time.sleep(.05)
    status = comparison_status(directory)
    require(status is not None and status['state'] == 'running' and status['pid'] == status['pgid'] == os.getpid()
            and os.getpgrp() == os.getpid() and status['process_start'] == N._process_start(os.getpid())
            and _json(directory / 'launch.json') == dict(pid=os.getpid(), ppid=os.getppid(), pgid=os.getpid(),
                 process_start=status['process_start']))
    return (calculate or C.main)(['--request-stdin'], stdin=io.BytesIO(C.canonical(request)))


def render_comparison(result):
    """Render finite verified numbers, with explicit exploratory wording."""
    require(type(result) is dict and result.get('profile') == PROFILE and result.get('result') == 'passed'
            and result.get('cleanup_verified') is True and type(result.get('comparison')) is dict)
    value = result['comparison']
    text = lambda x: escape(str(x))
    number = lambda x: text(format(C._number(x), '.6g'))
    body = ''
    for group, label in (('train', '学習48行'), ('test', '再利用holdout16行')):
        for key, name in (('reference', '保存モデル'), ('candidate', '比較モデル')):
            item = value[key]
            cells = [text(label), text(name), number(item['alpha'])]
            cells += [number(item['metrics'][group][metric]) for metric in ('rmse', 'mae', 'r2')]
            body += '<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>'
    detail = ''.join('<tr><td>' + text(row['row_index']) + '</td>' + ''.join('<td>' + number(row[key]) + '</td>'
                    for key in ('actual', 'reference_prediction', 'candidate_prediction', 'prediction_delta')) + '</tr>'
                    for row in value['holdout'])
    return ('<section><h3>Ridge の条件比較</h3><p>' + text(C.SCOPE) + '</p><p>元の学習: <code>' +
            text(value['identity']['attempt_token']) + '</code><br>比較実行: <code>' + text(result['attempt_token']) +
            '</code></p><table><thead><tr><th>対象</th><th>モデル</th><th>alpha</th><th>RMSE</th><th>MAE</th><th>R²</th>' +
            '</tr></thead><tbody>' + body + '</tbody></table><p>予測差は比較モデル − 保存モデルです。</p>' +
            '<table><thead><tr><th>行ID</th><th>実測</th><th>保存モデル</th><th>比較モデル</th><th>予測差</th></tr></thead>' +
            '<tbody>' + detail + '</tbody></table><p>新しい MLflow run は作成していません。Notebook を保存して比較結果を残してください。</p></section>')


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
