"""Retained snapshot browsing and two serial, exact COUNT Readers; no model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import os
from pathlib import Path
import subprocess
import sys

from tools.local_experiment.notebook import client as N
from tools.local_experiment.notebook_assist import run as A
from tools.local_experiment.query_snapshots import contract as C
from tools.local_experiment.query_count import contract as COUNT, run as COUNT_RUN

ROOT = Path(__file__).resolve().parents[3]
PROFILE = 'interactive-local-snapshot-counts-v1'
ERROR_CODES = {'LAB_SNAPSHOT_FAILED', 'LAB_SNAPSHOT_SOURCE_CHANGED', 'INTERRUPTED'}


def require(value, code='LAB_SNAPSHOT_INVALID'):
    N._require(value, code)


def normalize_request(value):
    try:
        require(type(value) is dict)
        C.normalize_request({'command': 'snapshots', 'table': value.get('table')})
        if value.get('command') == 'snapshots':
            return C.normalize_request(value)
        require(value.get('command') == 'compare_counts' and set(value) == {'command', 'table', 'left_id', 'right_id'})
        C.snapshot_id(value['left_id']); C.snapshot_id(value['right_id'])
        require(value['left_id'] != value['right_id'])
        return dict(value)
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None


def count_request(request, side):
    request = normalize_request(request)
    require(request['command'] == 'compare_counts' and side in ('left', 'right'))
    return COUNT.normalize_request(dict(command='count', table=request['table'],
                                        snapshot_id=request[side + '_id'], where=None))


def comparison(request, left, right):
    """Only two complete compatible observations authorize a signed difference."""
    left = COUNT.validate_public(left, count_request(request, 'left'))
    right = COUNT.validate_public(right, count_request(request, 'right'))
    require(all(left[key] == right[key] for key in ('table', 'table_uuid', 'schema_sha256')))
    return dict(left=left, right=right, difference=str(int(right['count']) - int(left['count'])),
                definition='right_count_minus_left_count')


def sources():
    value = COUNT_RUN.sources()
    paths = [Path(__file__), Path(__file__).with_name('query.py'), Path(N.__file__), Path(A.__file__),
             ROOT / 'go.mod', ROOT / 'go.sum', *sorted((ROOT / 'internal/cli').glob('*.go')),
             *sorted((ROOT / 'cmd/shirokuma').glob('*.go'))]
    value.update({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    return value


def _phases(request):
    return ('listing',) if request['command'] == 'snapshots' else ('left', 'right')


def _phase_request(request, phase):
    return request if phase == 'listing' else count_request(request, phase)


def _frames(raw, code, request, phase):
    return (N._snapshots_reader_frames(raw, code, request['table']) if phase == 'listing' else
            N._snapshot_count_reader_frames(raw, code, count_request(request, phase)))


def _closed(output, request, phase):
    return A._reader_closed(output / phase, _phase_request(request, phase),
                            lambda raw, code: _frames(raw, code, request, phase))


def _digest(binary):
    require(binary.is_file() and not binary.is_symlink() and binary.stat().st_size <= 32 * 1024 * 1024)
    return hashlib.sha256(binary.read_bytes()).hexdigest()


def _build(binary, log):
    subprocess.run(['go', 'build', '-o', str(binary), './cmd/shirokuma'], cwd=ROOT,
                   stdout=log, stderr=log, check=True, timeout=60)


def worker(directory, *, build=None, reader=None, source_factory=None, stdout=None):
    """The Lab parent owns this worker; Notebook client owns each serial Reader."""
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory); value = Q._input(directory)
    require(value['schema_version'] == 4)
    request = value['input']; phases = _phases(request)
    stdout = sys.stdout if stdout is None else stdout
    source_factory = sources if source_factory is None else source_factory
    output = directory / 'snapshots'; output.mkdir(mode=0o700)
    binary = output / 'shirokuma'; original = None
    report = dict(schema_version=1, profile=PROFILE, request=request, request_sha256=C.digest(request),
        result='running', error_code=None, model_calls=0, prepared=False, source_hashes=None, binary_sha256=None,
        started=[], reader_cleanup_confirmed=False, listing=None, left=None, right=None, difference=None, observed_at=None)

    def unchanged():
        require(Q._input(directory) == value and source_factory() == report['source_hashes'], 'LAB_SNAPSHOT_SOURCE_CHANGED')
        if report['binary_sha256'] is not None:
            require(_digest(binary) == report['binary_sha256'], 'LAB_SNAPSHOT_SOURCE_CHANGED')

    try:
        with A.A._signals():
            report['source_hashes'] = source_factory()
            N._write(output / 'execution-intent.json', dict(schema_version=1, profile=PROFILE,
                request_sha256=report['request_sha256'], source_hashes=report['source_hashes']))
            report['prepared'] = True
            with (output / 'build.log').open('xb') as log:
                os.fchmod(log.fileno(), 0o600); (build or _build)(binary, log)
            report['binary_sha256'] = _digest(binary)
            for phase in phases:
                unchanged()
                report['started'].append(phase)
                target = output / phase; target.mkdir(mode=0o700)
                with A.reader_environment(target, binary):
                    argument = request['table'] if phase == 'listing' else count_request(request, phase)
                    call = reader or (N.read_snapshots if phase == 'listing' else N.read_snapshot_count)
                    public = call(argument)
                _, verified, cleanup = _frames(N._read(target / 'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2),
                                                0, request, phase)
                require(cleanup and verified is not None and public == verified and _closed(output, request, phase))
                report[phase] = verified
                unchanged()
            if request['command'] == 'compare_counts':
                report['difference'] = comparison(request, report['left'], report['right'])['difference']
            report['observed_at'] = A.stamp()
    except BaseException as error:
        original = error
    finally:
        try:
            if report['prepared']: unchanged()
        except BaseException as error:
            original = A.A._keep(original, error)
        try:
            report['reader_cleanup_confirmed'] = all(_closed(output, request, phase) for phase in report['started'])
        except BaseException as error:
            original = A.A._keep(original, error)
        report['result'] = ('unknown' if not report['reader_cleanup_confirmed'] else 'passed' if original is None
                            else 'failed' if isinstance(original, Exception) else 'interrupted')
        if original is not None:
            report['error_code'] = ('INTERRUPTED' if not isinstance(original, Exception) else str(original)
                if isinstance(original, N.NotebookError) and str(original) in ERROR_CODES else 'LAB_SNAPSHOT_FAILED')
        if report['result'] != 'passed':
            report['difference'] = report['observed_at'] = None
        try:
            N._write(output / 'owner-result.json', report)
        except BaseException as error:
            raise A.A._keep(original, error)
    frame = {key: report[key] for key in ('result', 'error_code', 'model_calls', 'reader_cleanup_confirmed')}
    frame['evidence'] = str((output / 'owner-result.json').relative_to(ROOT))
    stdout.write(N._canonical(frame).decode('ascii'))
    if original is not None and not isinstance(original, Exception):
        return 128 + original.signum if isinstance(original, A.A.AssetsInterrupted) else 130
    return 0 if report['result'] == 'passed' else 1


def _timestamp(value):
    require(type(value) is str and value.endswith('Z') and len(value) <= 40)
    try:
        require(datetime.fromisoformat(value[:-1] + '+00:00').utcoffset() == timedelta(0))
    except Exception:
        raise N.NotebookError('LAB_SNAPSHOT_INVALID') from None


def terminal_receipt(directory, exit_code, *, verify_source=False):
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory); value = Q._input(directory); require(value['schema_version'] == 4)
    request = value['input']; phases = _phases(request)
    raw = N._read(directory / 'worker.stdout.json', 16384)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1)
    frame = N._parse(raw)
    output = directory / 'snapshots'; Q._private_directory(output)
    evidence = str((output / 'owner-result.json').relative_to(ROOT))
    require(type(frame) is dict and set(frame) == {'result', 'error_code', 'model_calls', 'reader_cleanup_confirmed', 'evidence'}
        and frame['evidence'] == evidence and frame['result'] in ('passed', 'failed', 'interrupted', 'unknown')
        and (frame['error_code'] is None or frame['error_code'] in ERROR_CODES)
        and type(frame['model_calls']) is int and frame['model_calls'] == 0
        and type(frame['reader_cleanup_confirmed']) is bool and type(exit_code) is int
        and exit_code in {'passed': (0,), 'failed': (1,), 'interrupted': (130, 143), 'unknown': (1, 130, 143)}[frame['result']])
    report = Q._json(output / 'owner-result.json', N.C.MAX_PUBLIC_BYTES)
    require(type(report) is dict and set(report) == {'schema_version', 'profile', 'request', 'request_sha256',
        'result', 'error_code', 'model_calls', 'prepared', 'source_hashes', 'binary_sha256', 'started',
        'reader_cleanup_confirmed', 'listing', 'left', 'right', 'difference', 'observed_at'}
        and type(report['schema_version']) is int and report['schema_version'] == 1 and report['profile'] == PROFILE
        and report['request'] == request and report['request_sha256'] == C.digest(request)
        and all(type(report[k]) is type(frame[k]) and report[k] == frame[k] for k in
                ('result', 'error_code', 'model_calls', 'reader_cleanup_confirmed'))
        and type(report['prepared']) is bool and type(report['started']) is list
        and report['started'] == list(phases[:len(report['started'])]))
    if report['prepared']:
        sealed = report['source_hashes']
        require(type(sealed) is dict and all(type(k) is str and type(v) is str and N.SHA.fullmatch(v) for k, v in sealed.items())
            and all(k in sealed for k in ('tools/local_experiment/notebook_lab/snapshots.py',
                'tools/local_experiment/notebook_lab/query.py', 'tools/local_experiment/notebook/client.py',
                'tools/local_experiment/query_snapshots/contract.py', 'tools/local_experiment/query_count/contract.py'))
            and Q._json(output / 'execution-intent.json', N.C.MAX_PUBLIC_BYTES) == dict(schema_version=1,
                profile=PROFILE, request_sha256=report['request_sha256'], source_hashes=sealed))
        if verify_source and report['result'] == 'passed': require(sealed == sources())
    else:
        require(report['result'] in ('failed', 'interrupted') and not report['started']
                and not (output / 'execution-intent.json').exists())
    if report['started']:
        require(report['prepared'] and type(report['binary_sha256']) is str and N.SHA.fullmatch(report['binary_sha256']))
    closed = True
    for phase in ('listing', 'left', 'right'):
        target = output / phase
        if phase not in report['started']:
            require(report[phase] is None and not target.exists() and not target.is_symlink())
            continue
        phase_closed = _closed(output, request, phase)
        closed = closed and phase_closed
        if report[phase] is not None:
            require(phase_closed)
            _, public, cleanup = _frames(N._read(target / 'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request, phase)
            require(cleanup and public is not None and public == report[phase])
        if phase == 'right':
            require(report['left'] is not None and _closed(output, request, 'left'))
    require(not report['reader_cleanup_confirmed'] or closed)
    compared = None
    if request['command'] == 'compare_counts':
        compared = dict(left=report['left'], right=report['right'], difference=None, definition='right_count_minus_left_count')
    if frame['result'] == 'passed':
        require(frame['error_code'] is None and frame['reader_cleanup_confirmed'] and report['started'] == list(phases)
                and all(report[phase] is not None for phase in phases))
        _timestamp(report['observed_at'])
        if compared is not None:
            compared = comparison(request, report['left'], report['right'])
            require(report['difference'] == compared['difference'])
        else:
            require(report['difference'] is None)
    else:
        require(frame['error_code'] is not None and report['difference'] is None and report['observed_at'] is None)
    return directory.name, dict(schema_version=4, mode='snapshots', command=request['command'], result=frame['result'],
        attempt_token=directory.name, evidence=evidence, snapshots=report['listing'] if frame['result'] == 'passed' else None,
        comparison=compared, observed_at=report['observed_at'], model_calls=0, cleanup_verified=frame['reader_cleanup_confirmed'])


def recover(attempt_token):
    from tools.local_experiment.notebook_lab import query as Q
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token))
        directory = Q._directory(Q.STATE / attempt_token)
        require(Q._input(directory)['schema_version'] == 4)
        status = Q.query_status(directory)
        require(status is not None and status['state'] == 'passed' and status['child_exited'] and status['cleanup_verified'])
        token, public = terminal_receipt(directory, status['exit_code'])
        require(token == status['evidence_token'] and public['result'] == 'passed' and public['cleanup_verified']
                and Q._json(directory / 'result.json', N.C.MAX_PUBLIC_BYTES) == public)
        return dict(public, saved_result=True)
    except Exception:
        raise N.NotebookError('LAB_SNAPSHOT_INVALID') from None


def render(result):
    try:
        require(type(result) is dict and result.get('schema_version') == 4 and result.get('mode') == 'snapshots'
            and result.get('result') == 'passed' and result.get('cleanup_verified') is True
            and type(result.get('model_calls')) is int and result['model_calls'] == 0
            and type(result.get('saved_result', False)) is bool)
        _timestamp(result.get('observed_at'))
        if result.get('command') == 'snapshots':
            public = C.validate_public(result['snapshots']); require(result.get('comparison') is None)
            body = '<h3>保持されている snapshot: ' + escape(public['table']) + '</h3>'
            if not public['snapshots']:
                body += '<p>保持されている snapshot はありません。</p>'
            else:
                body += '<table><thead><tr><th>snapshot ID</th><th>記録時刻（UTC）</th><th>操作</th><th>current</th><th>schema 互換性</th></tr></thead><tbody>'
                for record in public['snapshots']:
                    stamp = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=record['timestamp_ms'])).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                    body += '<tr><td><code>' + escape(record['snapshot_id']) + '</code></td><td>' + stamp + '</td><td>' + escape(record['operation']) + '</td><td>' + ('はい' if record['is_current'] else '') + '</td><td>' + escape(record['schema_compatibility']) + '</td></tr>'
                body += '</tbody></table>'
        else:
            require(result.get('command') == 'compare_counts' and result.get('snapshots') is None)
            compared = result['comparison']; require(type(compared) is dict)
            left, right = compared['left'], compared['right']
            request = normalize_request(dict(command='compare_counts', table=left['table'].removeprefix('iceberg.'),
                                             left_id=left['snapshot_id'], right_id=right['snapshot_id']))
            require(compared == comparison(request, left, right))
            body = '<h3>snapshot の件数比較: ' + escape(left['table']) + '</h3><table><thead><tr><th></th><th>snapshot ID</th><th>件数</th></tr></thead><tbody>'
            for label, public in (('左', left), ('右', right)):
                body += '<tr><td>' + label + '</td><td><code>' + escape(public['snapshot_id']) + '</code></td><td>' + escape(public['count']) + '</td></tr>'
            body += '</tbody></table><p>件数の差（右 − 左）: <strong>' + escape(compared['difference']) + '</strong></p><p>追加・削除された行の内訳や、行内容の一致を示す値ではありません。</p>'
        return body + '<p>取得完了時刻（UTC）: <code>' + escape(result['observed_at']) + '</code><br>' + (
            '保存結果です。追加の読取は行っていません。' if result.get('saved_result') else '取得時点の結果です。') + (
            '<br>実行 token: <code>' + escape(str(result.get('attempt_token', ''))) + '</code><br>証跡: <code>'
            + escape(str(result.get('evidence', ''))) + '</code></p>')
    except Exception:
        raise N.NotebookError('LAB_SNAPSHOT_INVALID') from None
