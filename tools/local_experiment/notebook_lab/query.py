"""Interactive bounded preview through the existing local Assist worker.

Only the subprocess changes its environment or imports the DataOps runtime.
Notebook input/output is private user content; rows never enter the model.
Each call is a fresh attempt, without automatic retry or forced termination.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
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
from tools.local_experiment.notebook import client as N, train as T
from tools.local_experiment.notebook_assist import contract as C, filtered as F, run as A

LAB_STATE = ROOT / 'tmp/local-experiment/notebook-lab'
STATE = LAB_STATE / 'queries'
PYTHON = ROOT / 'tools/local_experiment/dataops/.venv/bin/python'
KUBECONFIG = Path.home() / '.kube/config'
PROFILE = 'interactive-local-query-v1'
SECONDS = 600
MAX_ATTEMPTS = 128
ERROR_CODES = {'LAB_QUERY_INVALID', 'LAB_QUERY_FAILED', 'LAB_QUERY_INTERRUPTED',
               'LAB_QUERY_CLEANUP_UNKNOWN', 'LAB_QUERY_BUSY'}


def require(condition, code='LAB_QUERY_INVALID'):
    N._require(condition, code)


def _private_directory(path):
    try:
        A.A._private_directory(path)
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None


def arguments(directory):
    return [str(PYTHON), '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)]


def _directory(value):
    directory = Path(value)
    require(directory.is_absolute() and directory.parent == STATE and N.TOKEN.fullmatch(directory.name))
    _private_directory(STATE)
    _private_directory(directory)
    return directory


def _json(path, maximum=16384):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    return N._parse(N._read(path, maximum))


def _input(directory):
    value = _json(directory / 'input.json', 16384)
    require(type(value) is dict and set(value) == {'schema_version', 'filtered', 'input'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['filtered']) is bool)
    value['input'] = C.normalize_input(value['input'])
    require(N._read(directory / 'input.json', 16384) == N._canonical(value))
    return value


def query_status(directory):
    directory = _directory(directory)
    path = directory / 'status.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _json(path)
    require(type(value) is dict and set(value) == T.STATUS_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] in ('pending', 'running', 'passed', 'unsupported', 'failed', 'interrupted', 'unknown')
            and all(type(value[k]) is bool for k in ('child_exited', 'cleanup_verified'))
            and all(value[k] is None or type(value[k]) is int and value[k] > 1 for k in ('pid', 'pgid'))
            and value['pid'] == value['pgid']
            and (value['process_start'] is None or type(value['process_start']) is str
                 and 1 <= len(value['process_start']) <= 64)
            and (value['evidence_token'] is None or type(value['evidence_token']) is str
                 and N.TOKEN.fullmatch(value['evidence_token']))
            and (value['exit_code'] is None or type(value['exit_code']) is int)
            and value['child_exited'] == (value['exit_code'] is not None)
            and (value['error_code'] is None or value['error_code'] in ERROR_CODES))
    require(not value['cleanup_verified'] or value['child_exited'])
    require(value['state'] not in ('passed', 'unsupported') or value['cleanup_verified']
            and value['exit_code'] == (0 if value['state'] == 'passed' else 2)
            and value['evidence_token'] is not None and value['error_code'] is None)
    return value


def terminal_receipt(directory, exit_code):
    """Rebind terminal owner, original input, cleanup and typed preview frames."""
    directory = _directory(directory)
    requested = _input(directory)
    filtered, request = requested['filtered'], requested['input']
    raw = N._read(directory / 'worker.stdout.json', 16384)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1)
    frame = N._parse(raw)
    require(type(frame) is dict and set(frame) == {'result', 'error_code', 'model_calls',
            'reader_cleanup_confirmed', 'evidence'} and frame['result'] in
            ('passed', 'unsupported', 'failed', 'interrupted', 'unknown')
            and type(frame['reader_cleanup_confirmed']) is bool
            and type(frame['model_calls']) is int and frame['model_calls'] in (0, 1)
            and (frame['error_code'] is None or frame['error_code'] in A.ERROR_CODES)
            and type(exit_code) is int and exit_code in
            ({'passed': (0,), 'unsupported': (2,), 'failed': (1,),
              'interrupted': (130, 143), 'unknown': (1, 130, 143)}[frame['result']]))
    state = A.FILTERED_STATE if filtered else A.STATE
    prefix = str(state.relative_to(ROOT)) + '/'
    evidence = frame['evidence']
    require(type(evidence) is str and evidence.startswith(prefix) and evidence.endswith('/owner-result.json'))
    token = evidence[len(prefix):-len('/owner-result.json')]
    require(N.TOKEN.fullmatch(token) is not None)
    output = state / token
    _private_directory(output)
    owner = _json(output / 'owner-result.json')
    schema, profile = (2, F.PROFILE) if filtered else (1, A.PROFILE)
    require(type(owner) is dict and set(owner) == {'schema_version', 'profile', 'result', 'summary_result',
            'error_code', 'model_calls', 'reader_cleanup_confirmed'}
            and type(owner['schema_version']) is int and owner['schema_version'] == schema
            and owner['profile'] == profile and all(type(owner[k]) is type(frame[k]) and owner[k] == frame[k] for k in
            ('result', 'error_code', 'model_calls', 'reader_cleanup_confirmed')))
    preview = None
    if owner['summary_result'] is None:
        require(frame['result'] in ('failed', 'interrupted') and frame['model_calls'] == 0
                and frame['reader_cleanup_confirmed'] is True
                and all(not (output / p).exists() and not (output / p).is_symlink()
                        for p in ('summary.json', 'execution-intent.json', 'describe', 'assets')))
    else:
        summary = _json(output / 'summary.json', 256 * 1024)
        require(type(summary.get('schema_version')) is int and summary['schema_version'] == schema and summary.get('profile') == profile
                and summary.get('result') == owner['summary_result']
                and summary.get('input_sha256') == hashlib.sha256(N._canonical(request)).hexdigest()
                and _json(output / 'input.json') == request
                and type(summary.get('model_calls')) is int and summary['model_calls'] == frame['model_calls'])
        selection = summary.get('selection')
        closed = A.describe_closed(output / 'describe', request['table'])
        closed = closed and (A.A.reader_closed(output / 'assets', request=selection, filtered=filtered)
                             if selection is not None else not (output / 'assets').exists())
        require(not frame['reader_cleanup_confirmed'] or closed
                and summary.get('describe_cleanup_confirmed') is True
                and summary.get('asset_cleanup_confirmed') is True)
        if frame['result'] in ('passed', 'unsupported'):
            require(summary['result'] == frame['result'] and frame['error_code'] is None
                    and frame['model_calls'] == 1 and frame['reader_cleanup_confirmed'] is True
                    and summary.get('audit', {}).get('verified') is True
                    and summary.get('audit', {}).get('saved_spans_verified') is True)
            _, identity, closed = N._describe_reader_frames(
                N._read(output / 'describe/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request['table'])
            require(closed and identity is not None and identity == summary.get('description'))
            context = C.model_context(request, identity)
            response = _json(output / 'model-response.json')
            selected = (F if filtered else C).validate_proposal(response['content'], context)
            require(selected == selection)
            if frame['result'] == 'unsupported':
                require(selection is None and summary.get('asset_result') is None)
            else:
                require(selection is not None)
                reader = N._filtered_reader_frames if filtered else N._reader_frames
                _, preview, closed = reader(N._read(output / 'assets/reader.stdout.jsonl',
                                                    N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request=selection)
                require(closed and preview is not None and preview['table_uuid'] == identity['table_uuid']
                        and preview['schema_sha256'] == identity['schema_sha256'])
                if filtered:
                    predicate = A.QF.resolve_predicate(selection, identity['columns'])
                    require(preview['where'] == {k: predicate[k] for k in ('column', 'type')} | {'operator': 'eq'})
    public = {'schema_version': 1, 'result': frame['result'], 'attempt_token': directory.name,
              'evidence': evidence, 'preview': preview, 'model_calls': frame['model_calls'],
              'cleanup_verified': frame['reader_cleanup_confirmed']}
    return token, public


def query_closed(directory):
    directory = _directory(directory)
    value = query_status(directory)
    if value is None or not value['child_exited'] or not value['cleanup_verified']:
        return False
    if value['pid'] is not None and not T.group_empty(value['pid']):
        return False
    if value['evidence_token'] is None:
        return not (directory / 'launch.json').exists()
    token, public = terminal_receipt(directory, value['exit_code'])
    return token == value['evidence_token'] and public['cleanup_verified'] is True


def check_idle():
    """Read-only residual-attempt check; caller serializes with Lab owner.lock."""
    require(all(not p.is_symlink() for p in (STATE, *STATE.parents)))
    if not STATE.exists():
        return True
    _private_directory(STATE)
    candidates = list(STATE.iterdir())
    require(len(candidates) <= MAX_ATTEMPTS + 1)
    for path in candidates:
        if path.name == 'query.lock':
            continue
        require(N.TOKEN.fullmatch(path.name) is not None)
        if not query_closed(path):
            return False
    return True


def _check_lock(fd):
    s = os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
            and stat.S_IMODE(s.st_mode) == 0o600)


@contextmanager
def _query_lock():
    _private_directory(LAB_STATE)
    if not STATE.exists():
        STATE.mkdir(mode=0o700)
    _private_directory(STATE)
    fd = os.open(STATE / 'query.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    first = None
    try:
        _check_lock(fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise N.NotebookError('LAB_QUERY_BUSY') from None
        require(check_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
        yield
    except BaseException as error:
        first = error
    finally:
        try:
            os.close(fd)
        except BaseException as error:
            first = T._keep(first, error)
    if first is not None:
        raise first


@contextmanager
def _lock():
    # Parent only: the DataOps worker imports no Jupyter server/runtime module.
    from tools.local_experiment.notebook_lab import runtime as R
    first = None
    try:
        with R._lock():
            require(R._recordings_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
            require(R._trainings_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
            require(R._comparisons_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
            with _query_lock():
                try:
                    yield
                except BaseException as error:
                    first = error
                    raise
    except BaseException as error:
        first = T._keep(first, error)
        if not isinstance(first, Exception) or isinstance(first, N.NotebookError):
            raise first
        code = 'LAB_QUERY_BUSY' if isinstance(first, R.LabError) and str(first) == 'LAB_BUSY' else 'LAB_QUERY_FAILED'
        raise N.NotebookError(code) from None


def environment(directory):
    # No process-global mutations: only the child receives these values.
    return {'PATH': str(PYTHON.parent) + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
            'LC_ALL': 'C', 'TMPDIR': str(directory / 'tmp'),
            'KUBECONFIG': str(KUBECONFIG),
            'GOENV': 'off', 'GOTOOLCHAIN': 'local',
            'GOCACHE': str(LAB_STATE / 'query-cache/build'),
            'GOPATH': str(LAB_STATE / 'query-cache/gopath'),
            'GOMODCACHE': str(LAB_STATE / 'query-cache/modules'),
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'DO_NOT_TRACK': 'true', 'MLFLOW_DISABLE_TELEMETRY': 'true'}


def preview(table, request, *, filtered=False):
    """One local model proposal and, when supported, a verified table preview."""
    try:
        require(type(filtered) is bool)
        value = {'schema_version': 1, 'filtered': filtered,
                 'input': C.normalize_input({'table': table, 'request': request})}
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None
    with _lock():
        require(len(list(STATE.iterdir())) <= MAX_ATTEMPTS, 'LAB_QUERY_BUSY')
        for relative in ('query-cache', 'query-cache/build', 'query-cache/gopath', 'query-cache/modules'):
            path = LAB_STATE / relative
            if not path.exists():
                path.mkdir(mode=0o700)
            _private_directory(path)
        directory = STATE / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        (directory / 'tmp').mkdir(mode=0o700)
        N._write(directory / 'input.json', value)
        N._write(directory / 'intent.json', {'schema_version': 1, 'profile': PROFILE,
                 'argv': arguments(directory), 'input_sha256': hashlib.sha256(N._canonical(value)).hexdigest()})
        status = dict(schema_version=1, state='pending', pid=None, pgid=None, process_start=None,
                      evidence_token=None, exit_code=None, child_exited=False, cleanup_verified=False, error_code=None)
        child, first, public, interrupted = None, None, None, False
        try:
            N._write(directory / 'status.json', status, replace=True)
            with open(directory / 'worker.stdout.json', 'xb') as stdout, open(directory / 'worker.stderr.log', 'xb') as stderr:
                os.fchmod(stdout.fileno(), 0o600)
                os.fchmod(stderr.fileno(), 0o600)
                child = subprocess.Popen(arguments(directory), cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, env=environment(directory), start_new_session=True, umask=0o077)
                status.update(state='running', pid=child.pid, pgid=child.pid,
                              process_start=N._process_start(child.pid))
                N._write(directory / 'status.json', status, replace=True)
                N._write(directory / 'launch.json', {'pid': child.pid, 'ppid': os.getpid(),
                          'pgid': child.pid, 'process_start': status['process_start']})
                try:
                    child.wait(timeout=SECONDS)
                except BaseException as error:
                    first, interrupted = error, True
                    first = T._interrupt(child, first)
        except BaseException as error:
            first = T._keep(first, error)
            if child is not None and child.poll() is None and not interrupted:
                first = T._interrupt(child, first)
        finally:
            try:
                if child is None:
                    status.update(exit_code=1, child_exited=True, cleanup_verified=True)
                else:
                    status.update(exit_code=child.poll(), child_exited=child.poll() is not None)
                    if status['child_exited'] and T.group_empty(child.pid):
                        if not (directory / 'launch.json').exists():
                            status['cleanup_verified'] = True
                        else:
                            token, public = terminal_receipt(directory, child.returncode)
                            status.update(evidence_token=token, cleanup_verified=public['cleanup_verified'])
                require(status['cleanup_verified'], 'LAB_QUERY_CLEANUP_UNKNOWN')
                if first is None:
                    require(public is not None and public['result'] in ('passed', 'unsupported'), 'LAB_QUERY_FAILED')
                    N._write(directory / 'result.json', public)
            except BaseException as error:
                first = T._keep(first, error)
            status.update(state=public['result'] if first is None else 'unknown' if not status['cleanup_verified'] else
                          'interrupted' if not isinstance(first, Exception) else 'failed',
                          error_code=None if first is None else 'LAB_QUERY_INTERRUPTED' if not isinstance(first, Exception)
                          else 'LAB_QUERY_CLEANUP_UNKNOWN' if not status['cleanup_verified'] else 'LAB_QUERY_FAILED')
            try:
                N._write(directory / 'status.json', status, replace=True)
            except BaseException as error:
                first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception):
                raise first
            raise N.NotebookError(status['error_code'] or 'LAB_QUERY_FAILED') from None
        return public


def worker(directory, *, assist=None):
    directory = _directory(directory)
    value = _input(directory)
    require(_json(directory / 'intent.json') == {'schema_version': 1, 'profile': PROFILE,
            'argv': arguments(directory), 'input_sha256': hashlib.sha256(N._canonical(value)).hexdigest()})
    deadline = time.monotonic() + 30
    while not (directory / 'launch.json').exists():
        require(time.monotonic() < deadline)
        time.sleep(.05)
    status = query_status(directory)
    require(status is not None and status['state'] == 'running'
            and status['pid'] == status['pgid'] == os.getpid() and os.getpgrp() == os.getpid()
            and status['process_start'] == N._process_start(os.getpid())
            and _json(directory / 'launch.json') == {'pid': os.getpid(), 'ppid': os.getppid(),
                'pgid': os.getpid(), 'process_start': status['process_start']})
    return (assist or A.main)(['--filtered-request-stdin' if value['filtered'] else '--request-stdin'],
                             stdin=io.BytesIO(N._canonical(value['input'])))


def render_preview(result):
    """Plain escaped table; data and model output never become HTML or code."""
    require(type(result) is dict and result.get('result') in ('passed', 'unsupported')
            and result.get('cleanup_verified') is True)
    detail = '<p>実行 token: <code>' + escape(str(result.get('attempt_token', ''))) + '</code><br>証跡: <code>' + escape(str(result.get('evidence', ''))) + '</code></p>'
    if result['result'] == 'unsupported':
        return '<h3>対象外の依頼</h3><p>列・件数・対応する条件を明示してください。データの SELECT は実行していません。</p>' + detail
    public = result['preview']
    names = [column['name'] for column in public['columns']]
    header = ''.join('<th>' + escape(name) + '</th>' for name in names)
    def cell(value):
        require(type(value) is dict and set(value) == {'type', 'value'})
        return '<td>' + escape('NULL' if value['value'] is None else str(value['value'])) + '</td>'
    rows = ''.join('<tr>' + ''.join(cell(value) for value in row) + '</tr>' for row in public['rows'])
    lead = '<h3>読取成功</h3><p>' + escape(str(public['table'])) + ' — ' + str(public['row_count']) + ' 行'
    lead += '（上限で表示を打ち切りました）' if public['truncated'] else ''
    return lead + '</p><table><thead><tr>' + header + '</tr></thead><tbody>' + rows + '</tbody></table>' + detail


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        require(len(argv) == 2 and argv[0] == '--worker')
        return worker(argv[1])
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
