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
COUNT_READER_STATE = ROOT / 'tmp/local-experiment/query-count'
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
    require(type(value) is dict and type(value.get('schema_version')) is int)
    if value['schema_version'] == 2:
        require(set(value) == {'schema_version', 'mode', 'input'} and value['mode'] == 'count')
    elif value['schema_version'] == 3:
        from tools.local_experiment.notebook_lab import metadata as M
        require(set(value) == {'schema_version', 'mode', 'input'} and value['mode'] == 'metadata')
        value['input'] = M.normalize_request(value['input'])
    else:
        require(set(value) == {'schema_version', 'filtered', 'input'}
                and value['schema_version'] == 1 and type(value['filtered']) is bool)
    if value['schema_version'] != 3:
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


def terminal_receipt(directory, exit_code, *, verify_count_source=False, verify_metadata_source=False):
    """Rebind terminal owner, original input, cleanup and typed Reader frames."""
    directory = _directory(directory)
    requested = _input(directory)
    if requested['schema_version'] == 3:
        from tools.local_experiment.notebook_lab import metadata as M
        return M.terminal_receipt(directory, exit_code, verify_source=verify_metadata_source)
    counting = requested['schema_version'] == 2
    filtered, request = requested.get('filtered', False), requested['input']
    if counting:
        from tools.local_experiment.notebook_assist import count as K
        from tools.local_experiment.query_count import contract as QC
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
    state = A.COUNT_STATE if counting else A.FILTERED_STATE if filtered else A.STATE
    prefix = str(state.relative_to(ROOT)) + '/'
    evidence = frame['evidence']
    require(type(evidence) is str and evidence.startswith(prefix) and evidence.endswith('/owner-result.json'))
    token = evidence[len(prefix):-len('/owner-result.json')]
    require(N.TOKEN.fullmatch(token) is not None)
    output = state / token
    _private_directory(output)
    owner = _json(output / 'owner-result.json')
    schema, profile = (3, K.PROFILE) if counting else (2, F.PROFILE) if filtered else (1, A.PROFILE)
    require(type(owner) is dict and set(owner) == {'schema_version', 'profile', 'result', 'summary_result',
            'error_code', 'model_calls', 'reader_cleanup_confirmed'}
            and type(owner['schema_version']) is int and owner['schema_version'] == schema
            and owner['profile'] == profile and all(type(owner[k]) is type(frame[k]) and owner[k] == frame[k] for k in
            ('result', 'error_code', 'model_calls', 'reader_cleanup_confirmed')))
    preview = None
    reader_name = 'count' if counting else 'assets'
    cleanup_key = 'count_cleanup_confirmed' if counting else 'asset_cleanup_confirmed'
    result_key = 'count_result' if counting else 'asset_result'
    if owner['summary_result'] is None:
        require(frame['result'] in ('failed', 'interrupted') and frame['model_calls'] == 0
                and frame['reader_cleanup_confirmed'] is True
                and all(not (output / p).exists() and not (output / p).is_symlink()
                        for p in ('summary.json', 'execution-intent.json', 'describe', reader_name)))
    else:
        summary = _json(output / 'summary.json', 256 * 1024)
        require(type(summary.get('schema_version')) is int and summary['schema_version'] == schema and summary.get('profile') == profile
                and summary.get('result') == owner['summary_result']
                and summary.get('input_sha256') == hashlib.sha256(N._canonical(request)).hexdigest()
                and _json(output / 'input.json') == request
                and type(summary.get('model_calls')) is int and summary['model_calls'] == frame['model_calls'])
        if counting:
            intent = _json(output / 'execution-intent.json', 256 * 1024)
            sources = summary.get('source_hashes')
            require(type(sources) is dict and sources and all(type(k) is str and type(v) is str
                    and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
                    for k, v in sources.items())
                    and 'tools/local_experiment/query_count/contract.py' in sources
                    and 'tools/local_experiment/query_count/run.py' in sources
                    and set(intent) == {'schema_version', 'profile', 'input_sha256', 'binary_sha256', 'source_hashes'}
                    and type(intent['schema_version']) is int and intent['schema_version'] == schema
                    and intent['profile'] == profile and intent['input_sha256'] == summary['input_sha256']
                    and type(intent['binary_sha256']) is str and len(intent['binary_sha256']) == 64
                    and all(c in '0123456789abcdef' for c in intent['binary_sha256'])
                    and intent['source_hashes'] == sources)
            if verify_count_source:
                require(sources == A.sources(count=True))
        selection = summary.get('selection')
        closed = A.describe_closed(output / 'describe', request['table'])
        closed = closed and ((A.count_closed(output / reader_name, selection) if counting else
                              A.A.reader_closed(output / reader_name, request=selection, filtered=filtered))
                             if selection is not None else not (output / reader_name).exists())
        require(not frame['reader_cleanup_confirmed'] or closed
                and summary.get('describe_cleanup_confirmed') is True
                and summary.get(cleanup_key) is True)
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
            selected = (K if counting else F if filtered else C).validate_proposal(response['content'], context)
            require(selected == selection)
            if frame['result'] == 'unsupported':
                require(selection is None and summary.get(result_key) is None)
            else:
                require(selection is not None)
                reader = N._count_reader_frames if counting else N._filtered_reader_frames if filtered else N._reader_frames
                _, preview, closed = reader(N._read(output / reader_name / 'reader.stdout.jsonl',
                                                    N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request=selection)
                require(closed and preview is not None and preview['table_uuid'] == identity['table_uuid']
                        and preview['schema_sha256'] == identity['schema_sha256'])
                if counting:
                    predicate = QC.resolve_predicate(selection, identity['columns'])
                    require(preview == summary.get(result_key) and preview['where'] ==
                            (None if predicate is None else {k: predicate[k] for k in ('column', 'type')} | {'operator': 'eq'}))
                elif filtered:
                    predicate = A.QF.resolve_predicate(selection, identity['columns'])
                    require(preview['where'] == {k: predicate[k] for k in ('column', 'type')} | {'operator': 'eq'})
    public = {'schema_version': 2 if counting else 1, 'result': frame['result'], 'attempt_token': directory.name,
              'evidence': evidence, 'count' if counting else 'preview': preview, 'model_calls': frame['model_calls'],
              'cleanup_verified': frame['reader_cleanup_confirmed']}
    if counting:
        public['mode'] = 'count'
    return token, public


def query_closed(directory):
    directory = _directory(directory)
    value = query_status(directory)
    if (directory / 'recovery.json').exists() or (directory / 'recovery.json').is_symlink():
        _saved_count_closure(directory)
        return True
    if value is None or not value['child_exited'] or not value['cleanup_verified']:
        return False
    if _input(directory)['schema_version'] in (2, 3) and value['state'] in ('passed', 'unsupported'):
        # The parent verified its owned process group before saving this result.
        # A later process with the same PID is not this completed COUNT's owner.
        token, public = terminal_receipt(directory, value['exit_code'])
        return (token == value['evidence_token'] and public['cleanup_verified'] is True
                and public['result'] == value['state']
                and _json(directory / 'result.json', 256 * 1024) == public)
    if value['pid'] is not None and not T.group_empty(value['pid']):
        return False
    if value['evidence_token'] is None:
        return not (directory / 'launch.json').exists()
    token, public = terminal_receipt(directory, value['exit_code'])
    return token == value['evidence_token'] and public['cleanup_verified'] is True


def _idle(except_token=None):
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
        if path.name == except_token:
            continue
        if not query_closed(path):
            return False
    return True


def check_idle():
    """Read-only residual-attempt check; caller serializes with Lab owner.lock."""
    return _idle()


def _check_lock(fd):
    s = os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
            and stat.S_IMODE(s.st_mode) == 0o600)


@contextmanager
def _query_lock(*, recovery_token=None):
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
        require(check_idle() if recovery_token is None else _idle(recovery_token), 'LAB_QUERY_CLEANUP_UNKNOWN')
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
def _lock(*, recovery_token=None):
    # Parent only: the DataOps worker imports no Jupyter server/runtime module.
    from tools.local_experiment.notebook_lab import runtime as R
    first = None
    try:
        with R._lock():
            require(R._recordings_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
            require(R._trainings_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
            require(R._comparisons_idle(), 'LAB_QUERY_CLEANUP_UNKNOWN')
            with _query_lock(recovery_token=recovery_token):
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
    return _execute(value)


def count(table, request):
    """One explicit COUNT request for all rows or one typed equality at current head."""
    try:
        value = {'schema_version': 2, 'mode': 'count',
                 'input': C.normalize_input({'table': table, 'request': request})}
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None
    return _execute(value)


def tables(schema):
    """List one fixed local schema without a model call."""
    from tools.local_experiment.notebook_lab import metadata as M
    return _execute({'schema_version': 3, 'mode': 'metadata',
                     'input': M.normalize_request({'command': 'tables', 'schema': schema})})


def describe(table):
    """Read one explicitly selected table's recorded column types and identity."""
    from tools.local_experiment.notebook_lab import metadata as M
    return _execute({'schema_version': 3, 'mode': 'metadata',
                     'input': M.normalize_request({'command': 'describe', 'table': table})})


def recover_metadata(attempt_token):
    from tools.local_experiment.notebook_lab import metadata as M
    return M.recover(attempt_token)


def render_metadata(result):
    from tools.local_experiment.notebook_lab import metadata as M
    return M.render(result)


def _execute(value):
    """Both modes share the same locks, worker ownership and residual checks."""
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
                            token, public = terminal_receipt(directory, child.returncode,
                                verify_count_source=value['schema_version'] == 2,
                                verify_metadata_source=value['schema_version'] == 3)
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
    if value['schema_version'] == 3:
        from tools.local_experiment.notebook_lab import metadata as M
        return M.worker(directory)
    flag = '--count-request-stdin' if value['schema_version'] == 2 else (
        '--filtered-request-stdin' if value['filtered'] else '--request-stdin')
    return (assist or A.main)([flag],
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


def recover_count(attempt_token):
    """Verify and display a saved COUNT without another model call or table read."""
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token) is not None)
        directory = _directory(STATE / attempt_token)
        require(_input(directory)['schema_version'] == 2)
        status = query_status(directory)
        require(status is not None and status['state'] in ('passed', 'unsupported')
                and status['child_exited'] and status['cleanup_verified'])
        token, public = terminal_receipt(directory, status['exit_code'])
        require(token == status['evidence_token'] and public['cleanup_verified'] is True
                and public['result'] == status['state'] and _json(directory / 'result.json', 256 * 1024) == public)
        return dict(public, saved_result=True)
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None


def _count_recovery_files(directory, assist_token, reader_token):
    """Seal the original failed evidence; no status or saved output is replaced."""
    output = A.COUNT_STATE / assist_token
    reader = COUNT_READER_STATE / reader_token
    _private_directory(output)
    _private_directory(reader)
    paths = {'lab/' + name: directory / name for name in
             ('input.json', 'intent.json', 'launch.json', 'status.json', 'worker.stdout.json', 'worker.stderr.log')}
    paths.update({'assist/' + name: output / name for name in
        ('build.log', 'shirokuma', 'execution-intent.json', 'input.json', 'model-context.json',
         'model-response.json', 'owner-result.json', 'spans.jsonl', 'summary.json',
         'describe/reader-intent.json', 'describe/reader-status.json', 'describe/reader.stdout.jsonl',
         'count/reader-intent.json', 'count/reader-status.json', 'count/reader.stdout.jsonl')})
    paths.update({'reader/' + name: reader / name for name in ('run.lock', 'summary.json')})
    result = {}
    for name, path in paths.items():
        require(all(not p.is_symlink() for p in (path, *path.parents)))
        maximum = 32 * 1024 * 1024 if name == 'assist/shirokuma' else max(256 * 1024, N.C.MAX_PUBLIC_BYTES * 2 + 2)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
                    and stat.S_IMODE(before.st_mode) == (0o700 if name == 'assist/shirokuma' else 0o600)
                    and before.st_size <= maximum)
            raw = stream.read(maximum + 1)
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            require(len(raw) == before.st_size and all(getattr(before, key) == getattr(after, key)
                    for after in (os.fstat(stream.fileno()), path.lstat()) for key in fields))
        result[name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                            mode=stat.S_IMODE(before.st_mode), mtime_ns=before.st_mtime_ns)
    return result


def _unstarted_count_base(directory):
    """Only the exact failed-before-runtime COUNT case; this performs no process I/O."""
    from tools.local_experiment.query_count import contract as QC
    directory = _directory(directory)
    requested = _input(directory)
    require(requested['schema_version'] == 2 and not (directory / 'result.json').exists()
            and not (directory / 'result.json').is_symlink())
    status = query_status(directory)
    require(status is not None and status['state'] == 'unknown' and status['child_exited'] is True
            and status['exit_code'] == 1 and status['cleanup_verified'] is False
            and status['error_code'] == 'LAB_QUERY_CLEANUP_UNKNOWN' and status['evidence_token'] is not None
            and status['pid'] is not None and type(status['process_start']) is str)
    assist_token = status['evidence_token']
    output = A.COUNT_STATE / assist_token
    reader_status = _json(output / 'count/reader-status.json')
    require(type(reader_status) is dict and set(reader_status) == T.STATUS_KEYS
            and type(reader_status.get('schema_version')) is int and reader_status['schema_version'] == 1
            and reader_status['state'] == 'failed' and type(reader_status['pid']) is int and reader_status['pid'] > 1
            and type(reader_status['pgid']) is int and reader_status['pid'] == reader_status['pgid']
            and type(reader_status['process_start']) is str and 1 <= len(reader_status['process_start']) <= 64
            and '\n' not in reader_status['process_start'] and reader_status['child_exited'] is True
            and type(reader_status['exit_code']) is int and reader_status['exit_code'] == 1
            and reader_status['cleanup_verified'] is False and reader_status['error_code'] == 'NOTEBOOK_READER_FAILED'
            and type(reader_status['evidence_token']) is str and N.TOKEN.fullmatch(reader_status['evidence_token']))
    reader_token = reader_status['evidence_token']
    before = _count_recovery_files(directory, assist_token, reader_token)
    token, terminal = terminal_receipt(directory, 1)
    require(token == assist_token and terminal['result'] == 'unknown' and terminal['count'] is None
            and terminal['model_calls'] == 1 and terminal['cleanup_verified'] is False)
    require(_json(directory / 'intent.json') == dict(schema_version=1, profile=PROFILE,
            argv=arguments(directory), input_sha256=hashlib.sha256(N._canonical(requested)).hexdigest()))
    launch = _json(directory / 'launch.json')
    require(type(launch) is dict and set(launch) == {'pid', 'ppid', 'pgid', 'process_start'}
            and type(launch['pid']) is int and type(launch['pgid']) is int
            and launch['pid'] == status['pid'] and launch['pgid'] == status['pgid']
            and launch['process_start'] == status['process_start']
            and 1 <= len(status['process_start']) <= 64 and '\n' not in status['process_start']
            and type(launch['ppid']) is int and launch['ppid'] > 1)
    summary = _json(output / 'summary.json', 256 * 1024)
    request = N._count_request(summary.get('selection'))
    require(summary.get('result') == 'unknown' and summary.get('count_result') is None
            and summary.get('describe_cleanup_confirmed') is True and summary.get('count_cleanup_confirmed') is False
            and A.describe_closed(output / 'describe', requested['input']['table'])
            and _json(output / 'count/reader-intent.json') == dict(schema_version=1, command='query count', request=request))
    intent = _json(output / 'execution-intent.json', 256 * 1024)
    require(intent['binary_sha256'] == before['assist/shirokuma']['sha256'])
    raw = N._read(output / 'count/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2)
    token, public, cleanup = N._count_reader_frames(raw, 1, request)
    require(token == reader_token and public is None and cleanup is False)
    child = _json(COUNT_READER_STATE / reader_token / 'summary.json', 256 * 1024)
    require(type(child) is dict and type(child.get('schema_version')) is int and child['schema_version'] == 4
            and child.get('profile') == QC.PROFILE and child.get('evidence_token') == reader_token
            and child.get('result') == 'failed' and child.get('error_code') == 'OBSERVATION_FAILED'
            and N._parse(raw.splitlines()[1])['error_code'] == child['error_code']
            and child.get('cleanup_verified') is False and child.get('request') == request
            and child.get('request_sha256') == QC.digest(request)
            and type(child.get('invocations')) is int and child['invocations'] == 1
            and all(type(child.get(k)) is int and child[k] == 0 for k in ('model_calls', 'cloud_calls', 'retries')))
    sources = child.get('source_hashes')
    require(type(sources) is dict and all(k in sources for k in
            ('tools/local_experiment/query_count/contract.py', 'tools/local_experiment/query_count/run.py'))
            and all(k in summary['source_hashes'] and summary['source_hashes'][k] == v for k, v in sources.items()))
    observation = child.get('observation')
    require(type(observation) is dict and set(observation) == {'state', 'request_sha256', 'inventory',
            'identity_before', 'identity_after', 'snapshot_catalog_before', 'snapshot_catalog_after',
            'selection', 'queries', 'query_drain', 'runtime', 'cleanup_verified', 'error_code'}
            and observation['state'] == 'failed' and observation['error_code'] == 'OBSERVATION_FAILED'
            and observation['request_sha256'] == QC.digest(request) and observation['cleanup_verified'] is False
            and observation['queries'] == {'worker': [], 'control': []}
            and observation['query_drain'] == {'verified': False}
            and observation['runtime'] == {'state': 'failed', 'trino': None, 'polaris': None, 'cleanup': {'verified': True}}
            and all(observation[k] is None for k in ('inventory', 'identity_before', 'identity_after',
                                                   'snapshot_catalog_before', 'snapshot_catalog_after', 'selection')))
    require(query_status(directory) == status and _count_recovery_files(directory, assist_token, reader_token) == before)
    processes = [{k: value[k] for k in ('pid', 'pgid', 'process_start')} for value in (status, reader_status)]
    return dict(schema_version=1, profile='notebook-count-unstarted-closure-v1', attempt_token=directory.name,
                assist_token=assist_token, reader_token=reader_token, result='failed', count=None,
                cleanup_verified=True, reason='COUNT_RUNTIME_NOT_STARTED', evidence=before, owned_processes=processes)


def _saved_count_closure(directory):
    """A saved closure is historical proof, independent of later PID/source reuse."""
    record = _json(directory / 'recovery.json', 256 * 1024)
    base = _unstarted_count_base(directory)
    require(type(record) is dict and set(record) == set(base) | {'closed_at'}
            and all(record[key] == value for key, value in base.items())
            and type(record['closed_at']) is str and 1 <= len(record['closed_at']) <= 64)
    return record


def close_unstarted_count(attempt_token):
    """Append closure for a failed COUNT that never acquired its runtime or sent SQL.

    No query, retry, signal or runtime mutation. Original failure files stay intact.
    This does not recover a count result; a subsequent count is a new attempt.
    """
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token) is not None)
        with _lock(recovery_token=attempt_token):
            directory = _directory(STATE / attempt_token)
            if (directory / 'recovery.json').exists() or (directory / 'recovery.json').is_symlink():
                return _saved_count_closure(directory)
            from tools.local_experiment.mlflow import runtime as M
            base = _unstarted_count_base(directory)
            rows = M.processes()
            require(all(owner['pid'] not in rows and not any(row['pgid'] == owner['pgid'] for row in rows.values())
                        for owner in base['owned_processes']))
            require(_unstarted_count_base(directory) == base)
            N._write(directory / 'recovery.json', dict(**base, closed_at=A.stamp()))
            return _saved_count_closure(directory)
    except Exception:
        raise N.NotebookError('LAB_QUERY_RECOVERY_INVALID') from None


def render_count(result):
    """Render the aggregate string and recorded snapshot, never preview rows."""
    from tools.local_experiment.query_count import contract as QC
    require(type(result) is dict and result.get('schema_version') == 2 and result.get('mode') == 'count'
            and result.get('result') in ('passed', 'unsupported') and result.get('cleanup_verified') is True
            and type(result.get('saved_result', False)) is bool)
    detail = '<p>実行 token: <code>' + escape(str(result.get('attempt_token', ''))) + '</code><br>証跡: <code>' + escape(str(result.get('evidence', ''))) + '</code></p>'
    saved = '<p>保存結果です。モデルやテーブルへの追加読取は行っていません。</p>' if result.get('saved_result') else ''
    if result['result'] == 'unsupported':
        require(result.get('count') is None)
        return '<h3>対象外の依頼</h3><p>全行または対応する単一等値条件の件数を指定してください。COUNT は実行していません。</p>' + saved + detail
    try:
        public = QC.validate_public(result['count'])
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None
    predicate = public['where']
    condition = '全行' if predicate is None else escape(predicate['column']) + '（' + escape(predicate['type']) + '）の等値条件'
    return ('<h3>件数の取得成功</h3>' + saved + '<p>' + escape(public['table']) + ': <strong>'
            + escape(public['count']) + '</strong> 件</p><p>条件: ' + condition
            + '<br>集計時の snapshot: <code>' + escape(public['snapshot_id'])
            + '</code></p>' + detail)


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
