"""Fixed notebook reads; no arbitrary SQL, model calls, or MLflow writes.

The runner supplies a private SHIROKUMA_NOTEBOOK_RUN_DIR and a built
SHIROKUMA_NOTEBOOK_BINARY. Reader stdout contains explicitly requested rows and
is private. MLflow responses are discarded after the safe projection. A second
call in the same run directory is refused; an unknown outcome is never retried.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import urllib.parse
import urllib.request

from tools.local_experiment.query_read import contract as C
from tools.local_experiment.notebook import selection as S
from tools.local_experiment.notebook import filtered_selection as FS
from tools.local_experiment.query_count import contract as QC

ROOT = Path(__file__).resolve().parents[3]
INDEX = ROOT / 'tmp/local-experiment/mlflow-tracking/import-index.json'
EXPERIMENT = 'shirokuma-local-evaluation-receipts-v1'
OWNER = 'shirokuma-local-evaluation-import-v1'
URL = 'http://127.0.0.1:18550'
REQUEST = {'command': 'select', 'table': 'smoke.fixture_v1', 'columns': ['id', 'name'], 'limit': 10}
READ_TIMEOUT = 240
CLEANUP_TIMEOUT = 245
MAX_HTTP = 2 * 1024 * 1024
MAX_RUNS = 128
TOKEN = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')


class NotebookError(ValueError):
    """Only fixed codes reach notebook tracebacks; never upstream bodies."""


def _require(condition, code):
    if not condition:
        raise NotebookError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii') + b'\n'


def _parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'NOTEBOOK_JSON_INVALID')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(NotebookError('NOTEBOOK_JSON_INVALID')))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise NotebookError('NOTEBOOK_JSON_INVALID') from None


def _directory():
    path = Path(os.environ.get('SHIROKUMA_NOTEBOOK_RUN_DIR', ''))
    _require(path.is_absolute() and all(not p.is_symlink() for p in (path, *path.parents)),
             'NOTEBOOK_DIRECTORY_INVALID')
    info = path.stat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700, 'NOTEBOOK_DIRECTORY_INVALID')
    return path


def _write(path, value, *, replace=False):
    temporary = path.with_name('.' + path.name + '.pending') if replace else path
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(_canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= maximum,
                 'NOTEBOOK_FILE_INVALID')
        raw = stream.read(maximum + 1)
    _require(len(raw) <= maximum, 'NOTEBOOK_FILE_INVALID')
    return raw


def _reader_envelope(raw, exit_code, command, *, schema_version=1, evidence_root='query-read'):
    """Validate a fixed Reader envelope, retaining the original v1 defaults."""
    lines = raw.splitlines()
    _require(len(lines) == 2 and raw.endswith(b'\n')
             and all(len(line) <= C.MAX_PUBLIC_BYTES for line in lines), 'NOTEBOOK_READER_INVALID')
    allocated, final = map(_parse, lines)
    _require(type(allocated) is dict and set(allocated) == {'schema_version', 'event', 'command', 'evidence_token'}
             and type(allocated['schema_version']) is int and allocated['schema_version'] == schema_version
             and allocated['event'] == 'allocated' and allocated['command'] == command
             and type(allocated['evidence_token']) is str and TOKEN.fullmatch(allocated['evidence_token']),
             'NOTEBOOK_READER_INVALID')
    token = allocated['evidence_token']
    _require(type(final) is dict and set(final) == {'schema_version', 'event', 'command', 'evidence_token',
             'result', 'observation', 'error_code', 'cleanup_verified', 'evidence'}
             and type(final['schema_version']) is int and final['schema_version'] == schema_version
             and final['event'] == 'result' and final['command'] == command
             and final['evidence_token'] == token
             and final['evidence'] == f'tmp/local-experiment/{evidence_root}/{token}/summary.json'
             and type(final['cleanup_verified']) is bool, 'NOTEBOOK_READER_INVALID')
    if final['result'] != 'passed':
        _require(final['observation'] is None and type(final['error_code']) is str
                 and re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', final['error_code'])
                 and ((final['result'] == 'failed' and exit_code == 1)
                      or (final['result'] == 'interrupted' and final['error_code'] == 'INTERRUPTED'
                          and exit_code in (130, 143))), 'NOTEBOOK_READER_UNVERIFIED')
        return token, None, final['cleanup_verified']
    _require(exit_code == 0 and final['error_code'] is None and final['cleanup_verified'] is True,
             'NOTEBOOK_READER_UNVERIFIED')
    _require(type(final['observation']) is dict, 'NOTEBOOK_READER_INVALID')
    return token, final['observation'], True


def _reader_frames(raw, exit_code, request=REQUEST):
    """Reuse Reader's typed row/hash validator after the exact public envelope."""
    request = S.normalize_request(request)
    token, public, cleanup = _reader_envelope(raw, exit_code, 'query select')
    if public is None:
        return token, None, cleanup
    try:
        C.receipt_projection(public)
        _require(public['table'] == 'iceberg.' + request['table']
                 and public['requested_limit'] == request['limit']
                 and [x['name'] for x in public['columns']] == request['columns'], 'NOTEBOOK_READER_INVALID')
    except C.ContractError:
        raise NotebookError('NOTEBOOK_READER_INVALID') from None
    return token, public, True


def _describe_request(table):
    try:
        return C.normalize_request({'command': 'describe', 'table': table})
    except C.ContractError:
        raise NotebookError('NOTEBOOK_REQUEST_INVALID') from None


def _describe_reader_frames(raw, exit_code, table):
    """Accept only the matching describe identity, never a select observation."""
    request = _describe_request(table)
    token, public, cleanup = _reader_envelope(raw, exit_code, 'query describe')
    if public is None:
        return token, None, cleanup
    try:
        public = C.validate_identity(public, request, public['columns'])
    except (C.ContractError, KeyError, TypeError):
        raise NotebookError('NOTEBOOK_READER_INVALID') from None
    return token, public, True


def _describe_arguments(binary, request):
    return [str(binary), '--repo-root', str(ROOT), '--profile', 'local-experiment',
            'query', 'describe', '--table', request['table']]


def _interrupt_and_wait(child, original):
    """Send one SIGINT to our Go session; never kill an unconfirmed owner."""
    try:
        os.killpg(child.pid, signal.SIGINT)
    except BaseException as error:
        if isinstance(original, Exception) and not isinstance(error, Exception):
            original = error
    deadline = time.monotonic() + CLEANUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if child.poll() is not None:
                break
            child.wait(timeout=min(1, max(0.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        except BaseException as error:
            if isinstance(original, Exception) and not isinstance(error, Exception):
                original = error
            if isinstance(error, Exception):
                break
    return original


def _process_start(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart='],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=2, env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    value = result.stdout.decode('ascii').strip()
    _require(result.returncode == 0 and 1 <= len(value) <= 64 and '\n' not in value,
             'NOTEBOOK_PROCESS_IDENTITY_UNKNOWN')
    return value


def _read_owned(request, arguments, validate_frames):
    """Own one fixed Reader call; public entry points supply its typed boundary.

    Writes reader-intent.json before spawn, reader.stdout.jsonl and an atomic
    reader-status.json. Unknown cleanup remains false even when Go has exited.
    KeyboardInterrupt survives signal/wait/status-save failures unchanged.
    """
    directory = _directory()
    binary = Path(os.environ.get('SHIROKUMA_NOTEBOOK_BINARY', ''))
    _require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(), 'NOTEBOOK_BINARY_INVALID')
    _write(directory / 'reader-intent.json', {'schema_version': 1, 'command': 'query ' + request['command'], 'request': request})
    status = dict(schema_version=1, state='pending', pid=None, pgid=None, process_start=None,
                  evidence_token=None, exit_code=None,
                  child_exited=False, cleanup_verified=False, error_code=None)
    child, original, public, interrupted = None, None, None, False
    try:
        _write(directory / 'reader-status.json', status, replace=True)
        fd = os.open(directory / 'reader.stdout.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            child = subprocess.Popen(arguments(binary, request),
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                cwd=ROOT, start_new_session=True,
                env={key: value for key, value in os.environ.items()
                                             if key in ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL', 'KUBECONFIG')})
            status.update(state='running', pid=child.pid, pgid=child.pid)
            _write(directory / 'reader-status.json', status, replace=True)
            try:
                status['process_start'] = _process_start(child.pid)
            except Exception:
                # A fast, already-reaped Reader no longer needs a live PID seal.
                if child.poll() is None:
                    raise
            _write(directory / 'reader-status.json', status, replace=True)
            try:
                child.wait(timeout=READ_TIMEOUT)
            except BaseException as error:
                interrupted = True
                original = error
                original = _interrupt_and_wait(child, original)
            output.flush()
            os.fsync(output.fileno())
        status.update(exit_code=child.poll(), child_exited=child.poll() is not None)
        if status['child_exited']:
            token, public, cleanup = validate_frames(_read(directory / 'reader.stdout.jsonl', C.MAX_PUBLIC_BYTES * 2 + 2),
                                                       child.returncode)
            status.update(evidence_token=token, cleanup_verified=cleanup)
            if public is None:
                raise NotebookError('NOTEBOOK_READER_FAILED')
        else:
            raise NotebookError('NOTEBOOK_READER_CLEANUP_UNKNOWN')
    except BaseException as error:
        if original is None or (isinstance(original, Exception) and not isinstance(error, Exception)):
            original = error
        try:
            if child is not None and child.poll() is None and not interrupted:
                # This also covers a status/fsync failure just after spawning.
                interrupted = True
                original = _interrupt_and_wait(child, original)
            if child is not None:
                status.update(exit_code=child.poll(), child_exited=child.poll() is not None)
        except BaseException as secondary:
            if isinstance(original, Exception) and not isinstance(secondary, Exception):
                original = secondary
    finally:
        status['state'] = ('passed' if original is None else
                           'interrupted' if not isinstance(original, Exception) else
                           'failed' if status['child_exited'] else 'unknown')
        status['error_code'] = (None if original is None else 'NOTEBOOK_INTERRUPTED'
                                if not isinstance(original, Exception) else 'NOTEBOOK_READER_FAILED')
        try:
            _write(directory / 'reader-status.json', status, replace=True)
        except BaseException as error:
            if original is None or (isinstance(original, Exception) and not isinstance(error, Exception)):
                original = error
    if original is not None:
        if not isinstance(original, Exception):
            raise original
        raise NotebookError('NOTEBOOK_READER_FAILED') from None
    return public


def read_select(request):
    """Run one typed current-head select through the owned Go Reader."""
    request = S.normalize_request(request)
    return _read_owned(request, lambda binary, value: S.arguments(binary, ROOT, value),
                       lambda raw, code: _reader_frames(raw, code, request=request))


def _filtered_reader_frames(raw, exit_code, request):
    request = FS.normalize_request(request)
    token, public, cleanup = _reader_envelope(raw, exit_code, 'query select',
                                             schema_version=3, evidence_root='query-filter')
    if public is None:
        return token, None, cleanup
    try:
        public = FS.F.validate_public(public, request)
    except FS.F.ContractError:
        raise NotebookError('NOTEBOOK_READER_INVALID') from None
    return token, public, True


def read_filtered(request):
    """One current-head schema-bound equality through the existing Reader owner."""
    request = FS.normalize_request(request)
    return _read_owned(request, lambda binary, value: FS.arguments(binary, ROOT, value),
                       lambda raw, code: _filtered_reader_frames(raw, code, request))


def _count_request(request):
    request = QC.normalize_request(request)
    _require(request['snapshot_id'] is None, 'NOTEBOOK_REQUEST_INVALID')
    return request


def _count_arguments(binary, request):
    request = _count_request(request)
    arguments = [str(binary), '--repo-root', str(ROOT), '--profile', 'local-experiment',
                 'query', 'count', '--table', request['table']]
    if request['where'] is not None:
        arguments += ['--where-column', request['where']['column'], '--equals', request['where']['literal']]
    return arguments


def _count_reader_frames(raw, exit_code, request):
    request = _count_request(request)
    token, public, cleanup = _reader_envelope(raw, exit_code, 'query count',
                                             schema_version=4, evidence_root='query-count')
    if public is None:
        return token, None, cleanup
    try: public = QC.validate_public(public, request)
    except QC.ContractError: raise NotebookError('NOTEBOOK_READER_INVALID') from None
    return token, public, True


def read_count(request):
    """One exact current-snapshot COUNT through the existing owned Go Reader."""
    request = _count_request(request)
    return _read_owned(request, _count_arguments,
                       lambda raw, code: _count_reader_frames(raw, code, request))


def read_describe(table):
    """Read only the named table's typed metadata through the owned Go Reader."""
    request = _describe_request(table)
    return _read_owned(request, _describe_arguments,
                       lambda raw, code: _describe_reader_frames(raw, code, request['table']))


def read_fixture():
    """Preserve the original fixed fixture entry point and request exactly."""
    return read_select(REQUEST)


def read_requested_table():
    """Read only the runner's canonical private request, bound before and after the child."""
    directory = _directory()
    path = directory / 'request.json'
    _require(os.environ.get('SHIROKUMA_NOTEBOOK_REQUEST_PATH') == str(path), 'NOTEBOOK_REQUEST_INVALID')
    expected = os.environ.get('SHIROKUMA_NOTEBOOK_REQUEST_SHA256', '')
    try:
        request = S.load_request(path, expected)
    except ValueError:
        raise NotebookError('NOTEBOOK_REQUEST_INVALID') from None
    public = read_select(request)
    try:
        _require(S.load_request(path, expected) == request, 'NOTEBOOK_REQUEST_INVALID')
    except ValueError:
        raise NotebookError('NOTEBOOK_REQUEST_INVALID') from None
    return public


def read_requested_filtered_table():
    """Use only the runner's immutable private filtered request; never print its literal."""
    directory = _directory()
    path = directory / FS.REQUEST_FILENAME
    _require(os.environ.get(FS.REQUEST_PATH_ENV) == str(path), 'NOTEBOOK_REQUEST_INVALID')
    expected = os.environ.get(FS.REQUEST_SHA_ENV, '')
    try:
        request = FS.load_request(path, expected)
    except ValueError:
        raise NotebookError('NOTEBOOK_REQUEST_INVALID') from None
    public = read_filtered(request)
    try:
        _require(FS.load_request(path, expected) == request, 'NOTEBOOK_REQUEST_INVALID')
    except ValueError:
        raise NotebookError('NOTEBOOK_REQUEST_INVALID') from None
    return public


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise NotebookError('NOTEBOOK_MLFLOW_READ_FAILED')


def _rest(path, payload=None):
    """Two fixed read-only REST endpoints; no proxies, redirects or retries."""
    lookup = '/api/2.0/mlflow/experiments/get-by-name?' + urllib.parse.urlencode({'experiment_name': EXPERIMENT})
    _require((path == lookup and payload is None) or
             (path == '/api/2.0/mlflow/runs/search' and type(payload) is dict), 'NOTEBOOK_MLFLOW_REQUEST_INVALID')
    request = urllib.request.Request(URL + path, data=None if payload is None else _canonical(payload),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=10) as response:
            _require(response.status == 200, 'NOTEBOOK_MLFLOW_READ_FAILED')
            raw = response.read(MAX_HTTP + 1)
        _require(len(raw) <= MAX_HTTP, 'NOTEBOOK_MLFLOW_RESPONSE_LIMIT')
        return _parse(raw)
    except Exception:
        raise NotebookError('NOTEBOOK_MLFLOW_READ_FAILED') from None


def _mapping(items):
    _require(type(items) is list and len(items) <= 256, 'NOTEBOOK_MLFLOW_RESPONSE_INVALID')
    result = {}
    for item in items:
        _require(type(item) is dict and type(item.get('key')) is str and 'value' in item
                 and item['key'] not in result, 'NOTEBOOK_MLFLOW_RESPONSE_INVALID')
        result[item['key']] = item['value']
    return result


def _evaluation_rows(response, experiment_id, index):
    _require(type(response) is dict and not response.get('next_page_token')
             and set(response) <= {'runs', 'next_page_token'} and type(response.get('runs')) is list
             and len(response['runs']) <= MAX_RUNS, 'NOTEBOOK_MLFLOW_INCOMPLETE')
    expected = {row['run_id']: (source, row) for source, row in index['runs'].items()}
    _require(len(expected) == len(index['runs']) and all(row['completed'] is True for _, row in expected.values()),
             'NOTEBOOK_MLFLOW_INCOMPLETE')
    rows, seen = [], set()
    for run in response['runs']:
        info, data = run['info'], run['data']
        run_id = info['run_id']
        _require(run_id in expected and run_id not in seen and info['experiment_id'] == experiment_id
                 and info['lifecycle_stage'] == 'active' and info['status'] in ('FINISHED', 'FAILED'),
                 'NOTEBOOK_MLFLOW_IDENTITY_INVALID')
        seen.add(run_id)
        source, entry = expected[run_id]
        params, tags, metrics = (_mapping(data[key]) for key in ('params', 'tags', 'metrics'))
        _require(tags.get('shirokuma.owner') == OWNER and tags.get('shirokuma.store_id') == index['store_id']
                 and tags.get('shirokuma.source_id') == source
                 and tags.get('shirokuma.source_key') == entry['source_key']
                 and tags.get('shirokuma.projection_sha256') == entry['projection_sha256']
                 and tags.get('shirokuma.time_semantics') == 'import-time-not-source-execution',
                 'NOTEBOOK_MLFLOW_IDENTITY_INVALID')
        row = {'run_id': run_id}
        for key in ('suite', 'provider_kind', 'provider', 'model', 'evaluation_suite'):
            value = params[key]
            _require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_./:-]{1,128}', value), 'NOTEBOOK_MLFLOW_RESPONSE_INVALID')
            row[key] = value
        _require(type(params['case_signature']) is str and SHA.fullmatch(params['case_signature'])
                 and params['source_result'] in ('passed', 'failed', 'interrupted'), 'NOTEBOOK_MLFLOW_RESPONSE_INVALID')
        row.update(case_signature=params['case_signature'], source_result=params['source_result'], cost=None)
        def number(key):
            value = metrics.get(key)
            _require(value is None or (type(value) in (int, float) and math.isfinite(value)
                     and 0 <= value <= 2 ** 53), 'NOTEBOOK_MLFLOW_RESPONSE_INVALID')
            return value
        row.update(requested_inferences=number('requested_inferences'),
                   attempted_inferences=number('attempted_inferences'))
        row['functional_success_observed'] = {part: number('functional_success_observed.' + part)
                                               for part in ('numerator', 'denominator', 'value')}
        row['usage'] = {kind: {part: number(kind + '.' + part)
                              for part in ('total', 'known_subtotal', 'known_calls', 'missing_calls')}
                        for kind in ('prompt_tokens', 'completion_tokens')}
        rows.append(row)
    _require(seen == set(expected), 'NOTEBOOK_MLFLOW_INCOMPLETE')
    return sorted(rows, key=lambda row: (row['suite'], row['provider'], row['case_signature'], row['run_id']))


def read_evaluations():
    """Return the saved evaluation experiment's safe latest-metric display.

    No metric history, new evaluation, model call, artifact download or writes.
    Missing usage/cost stays None. Import times are not inference durations.
    """
    directory = _directory()
    _write(directory / 'evaluation-intent.json', {'schema_version': 1, 'experiment': EXPERIMENT})
    try:
        index_raw = _read(INDEX, MAX_HTTP)
        index = _parse(index_raw)
        _require(index['experiment_pending'] is False and type(index['runs']) is dict
                 and len(index['runs']) <= MAX_RUNS, 'NOTEBOOK_MLFLOW_INCOMPLETE')
        path = '/api/2.0/mlflow/experiments/get-by-name?' + urllib.parse.urlencode({'experiment_name': EXPERIMENT})
        response = _rest(path)
        experiment = response['experiment']
        _require(experiment['experiment_id'] == index['experiment_id'] and experiment['name'] == EXPERIMENT
                 and experiment['lifecycle_stage'] == 'active'
                 and _mapping(experiment['tags']) == {'shirokuma.owner': OWNER, 'shirokuma.store_id': index['store_id']},
                 'NOTEBOOK_MLFLOW_IDENTITY_INVALID')
        response = _rest('/api/2.0/mlflow/runs/search', {'experiment_ids': [index['experiment_id']],
                         'run_view_type': 'ACTIVE_ONLY', 'max_results': MAX_RUNS + 1, 'order_by': ['attributes.run_id ASC']})
        rows = _evaluation_rows(response, index['experiment_id'], index)
        _require(_read(INDEX, MAX_HTTP) == index_raw, 'NOTEBOOK_MLFLOW_INDEX_CHANGED')
        public = dict(experiment=EXPERIMENT, experiment_id=index['experiment_id'], count=len(rows), runs=rows,
                      scope='saved-evaluation-latest-metrics; no fresh inference or audit verification')
        _write(directory / 'evaluations.json', public)
        return public
    except Exception:
        raise NotebookError('NOTEBOOK_MLFLOW_READ_FAILED') from None
