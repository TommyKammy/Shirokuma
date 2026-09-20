"""Model-free Lab metadata using the existing owned Notebook Reader."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import escape
import os
from pathlib import Path
import subprocess
import sys

from tools.local_experiment.notebook import client as N
from tools.local_experiment.notebook_assist import run as A
from tools.local_experiment.query_read import run as R

ROOT = Path(__file__).resolve().parents[3]
PROFILE = 'interactive-local-metadata-reader-v1'
ERROR_CODES = {'LAB_METADATA_FAILED', 'LAB_METADATA_SOURCE_CHANGED', 'INTERRUPTED'}


def require(value, code='LAB_METADATA_INVALID'):
    N._require(value, code)


def normalize_request(value):
    try:
        value = N.C.normalize_request(value)
        require(value['command'] in ('tables', 'describe'))
        return value
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None


def sources():
    value = R.sources()
    paths = [Path(__file__), Path(__file__).with_name('query.py'), Path(N.__file__), Path(A.__file__),
             ROOT / 'go.mod', ROOT / 'go.sum', *sorted((ROOT / 'internal/cli').glob('*.go')),
             *sorted((ROOT / 'cmd/shirokuma').glob('*.go'))]
    value.update({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    return value


def _frames(raw, code, request):
    return (N._tables_reader_frames(raw, code, request['schema']) if request['command'] == 'tables'
            else N._describe_reader_frames(raw, code, request['table']))


def _closed(output, request):
    return A._reader_closed(output / 'reader', request, lambda raw, code: _frames(raw, code, request))


def _binary_digest(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 32 * 1024 * 1024)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(binary, log):
    subprocess.run(['go', 'build', '-o', str(binary), './cmd/shirokuma'], cwd=ROOT,
                   stdout=log, stderr=log, check=True, timeout=60)


def worker(directory, *, build=None, reader=None, source_factory=None, stdout=None):
    """Run only in the already-owned Lab worker; injectable dependencies are for tests."""
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory)
    value = Q._input(directory)
    require(value['schema_version'] == 3)
    request = value['input']
    stdout = sys.stdout if stdout is None else stdout
    source_factory = sources if source_factory is None else source_factory
    output = directory / 'metadata'; output.mkdir(mode=0o700)
    binary = output / 'shirokuma'
    original = None
    report = dict(schema_version=1, profile=PROFILE, request=request, request_sha256=N.C.digest(request),
                  result='running', error_code=None, model_calls=0, prepared=False, source_hashes=None,
                  binary_sha256=None, reader_started=False, reader_cleanup_confirmed=False,
                  metadata=None, observed_at=None)

    def unchanged():
        require(Q._input(directory) == value, 'LAB_METADATA_SOURCE_CHANGED')
        require(source_factory() == report['source_hashes'], 'LAB_METADATA_SOURCE_CHANGED')
        if report['binary_sha256'] is not None:
            require(_binary_digest(binary) == report['binary_sha256'], 'LAB_METADATA_SOURCE_CHANGED')

    try:
        with A.A._signals():
            report['source_hashes'] = source_factory()
            N._write(output / 'execution-intent.json', dict(schema_version=1, profile=PROFILE,
                request_sha256=report['request_sha256'], source_hashes=report['source_hashes']))
            report['prepared'] = True
            with (output / 'build.log').open('xb') as log:
                os.fchmod(log.fileno(), 0o600)
                (build or _build)(binary, log)
            report['binary_sha256'] = _binary_digest(binary)
            unchanged()
            read_dir = output / 'reader'; read_dir.mkdir(mode=0o700)
            report['reader_started'] = True
            with A.reader_environment(read_dir, binary):
                call = reader or (N.read_tables if request['command'] == 'tables' else N.read_describe)
                public = call(request['schema'] if request['command'] == 'tables' else request['table'])
            _, verified, cleanup = _frames(N._read(read_dir / 'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request)
            require(cleanup and verified is not None and public == verified)
            require(_closed(output, request))
            report['observed_at'] = A.stamp()
            report['metadata'] = verified
            unchanged()
    except BaseException as error:
        original = error
    finally:
        try:
            if report['prepared']:
                unchanged()
        except BaseException as error:
            original = A.A._keep(original, error)
        try:
            report['reader_cleanup_confirmed'] = _closed(output, request)
        except BaseException as error:
            original = A.A._keep(original, error)
        if not report['reader_cleanup_confirmed']:
            report['result'] = 'unknown'
        elif original is None:
            report['result'] = 'passed'
        else:
            report['result'] = 'failed' if isinstance(original, Exception) else 'interrupted'
        if original is not None:
            report['error_code'] = ('INTERRUPTED' if not isinstance(original, Exception) else
                str(original) if isinstance(original, N.NotebookError) and str(original) in ERROR_CODES else 'LAB_METADATA_FAILED')
        if report['result'] != 'passed':
            report['metadata'] = report['observed_at'] = None
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
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
        require(parsed.utcoffset() == timezone.utc.utcoffset(None))
    except Exception:
        raise N.NotebookError('LAB_METADATA_INVALID') from None
    return value


def terminal_receipt(directory, exit_code, *, verify_source=False):
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory)
    value = Q._input(directory); require(value['schema_version'] == 3)
    request = value['input']
    raw = N._read(directory / 'worker.stdout.json', 16384)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1)
    frame = N._parse(raw)
    output = directory / 'metadata'; Q._private_directory(output)
    evidence = str((output / 'owner-result.json').relative_to(ROOT))
    require(type(frame) is dict and set(frame) == {'result', 'error_code', 'model_calls', 'reader_cleanup_confirmed', 'evidence'}
            and frame['evidence'] == evidence and frame['result'] in ('passed', 'failed', 'interrupted', 'unknown')
            and (frame['error_code'] is None or frame['error_code'] in ERROR_CODES)
            and type(frame['model_calls']) is int and frame['model_calls'] == 0
            and type(frame['reader_cleanup_confirmed']) is bool and type(exit_code) is int
            and exit_code in {'passed': (0,), 'failed': (1,), 'interrupted': (130, 143), 'unknown': (1, 130, 143)}[frame['result']])
    report = Q._json(output / 'owner-result.json', N.C.MAX_PUBLIC_BYTES)
    require(type(report) is dict and set(report) == {'schema_version', 'profile', 'request', 'request_sha256',
            'result', 'error_code', 'model_calls', 'prepared', 'source_hashes', 'binary_sha256', 'reader_started',
            'reader_cleanup_confirmed', 'metadata', 'observed_at'}
            and type(report['schema_version']) is int and report['schema_version'] == 1 and report['profile'] == PROFILE
            and report['request'] == request and report['request_sha256'] == N.C.digest(request)
            and all(type(report[k]) is type(frame[k]) and report[k] == frame[k] for k in
                    ('result', 'error_code', 'model_calls', 'reader_cleanup_confirmed'))
            and all(type(report[k]) is bool for k in ('prepared', 'reader_started')))
    if report['prepared']:
        sealed = report['source_hashes']
        require(type(sealed) is dict and all(type(k) is str and type(v) is str and N.SHA.fullmatch(v)
                for k, v in sealed.items()) and all(key in sealed for key in
                ('tools/local_experiment/notebook_lab/metadata.py', 'tools/local_experiment/notebook_lab/query.py',
                 'tools/local_experiment/notebook/client.py', 'tools/local_experiment/query_read/contract.py'))
                and Q._json(output / 'execution-intent.json', N.C.MAX_PUBLIC_BYTES) == dict(schema_version=1,
                    profile=PROFILE, request_sha256=report['request_sha256'], source_hashes=sealed))
        if verify_source and report['result'] == 'passed':
            require(sealed == sources())
    else:
        require(report['result'] in ('failed', 'interrupted') and not report['reader_started']
                and not (output / 'execution-intent.json').exists())
    if report['reader_started']:
        require(report['prepared'] and type(report['binary_sha256']) is str and N.SHA.fullmatch(report['binary_sha256']))
    else:
        require(not (output / 'reader').exists() and not (output / 'reader').is_symlink())
    require(not frame['reader_cleanup_confirmed'] or _closed(output, request))
    if frame['result'] == 'passed':
        require(frame['error_code'] is None and frame['reader_cleanup_confirmed'] and report['reader_started'])
        _, public, cleanup = _frames(N._read(output / 'reader/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request)
        require(cleanup and public is not None and public == report['metadata'])
        _timestamp(report['observed_at'])
    else:
        require(frame['error_code'] is not None and report['metadata'] is None and report['observed_at'] is None)
    return directory.name, dict(schema_version=3, mode='metadata', command=request['command'], result=frame['result'],
        attempt_token=directory.name, evidence=evidence, metadata=report['metadata'], observed_at=report['observed_at'],
        model_calls=0, cleanup_verified=frame['reader_cleanup_confirmed'])


def recover(attempt_token):
    from tools.local_experiment.notebook_lab import query as Q
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token))
        directory = Q._directory(Q.STATE / attempt_token)
        require(Q._input(directory)['schema_version'] == 3)
        status = Q.query_status(directory)
        require(status is not None and status['state'] == 'passed' and status['child_exited'] and status['cleanup_verified'])
        token, public = terminal_receipt(directory, status['exit_code'])
        require(token == status['evidence_token'] and public['result'] == 'passed' and public['cleanup_verified']
                and Q._json(directory / 'result.json', N.C.MAX_PUBLIC_BYTES) == public)
        return dict(public, saved_result=True)
    except Exception:
        raise N.NotebookError('LAB_METADATA_INVALID') from None


def _render(result):
    require(type(result) is dict and result.get('schema_version') == 3 and result.get('mode') == 'metadata'
            and result.get('result') == 'passed' and result.get('cleanup_verified') is True
            and type(result.get('model_calls')) is int and result['model_calls'] == 0
            and type(result.get('saved_result', False)) is bool)
    public = result.get('metadata')
    _timestamp(result.get('observed_at'))
    # Reuse the Reader's strict public projection without an additional read.
    if result.get('command') == 'tables':
        require(type(public) is dict)
        request = N._tables_request(public.get('schema'))
        validated = N._tables_public(public, request['schema'])
        require(validated == public)
        label = 'テーブル一覧' if public['complete'] else 'テーブル一覧（一部・上限100件）'
        body = '<p>テーブルはありません。</p>' if not public['tables'] else '<ul>' + ''.join(
            '<li><code>' + escape(name) + '</code></li>' for name in public['tables']) + '</ul>'
        body = '<h3>' + label + '</h3><p>schema: ' + escape(public['schema']) + '</p>' + body
    else:
        require(result.get('command') == 'describe' and type(public) is dict)
        request = N._describe_request(public.get('schema', '') + '.' + public.get('table', ''))
        N.C.validate_identity(public, request, public.get('columns'))
        body = '<h3>列情報: ' + escape(public['schema'] + '.' + public['table']) + '</h3>'
        body += '<table><thead><tr><th>列名</th><th>型</th><th>NULL 可</th></tr></thead><tbody>'
        body += ''.join('<tr><td>' + escape(c['name']) + '</td><td>' + escape(c['type'])
                        + '</td><td>' + ('可' if c['nullable'] else '不可') + '</td></tr>' for c in public['columns'])
        body += '</tbody></table><p>取得時の current snapshot: <code>' + escape(public['current_snapshot_id'] or 'なし') + '</code></p>'
    return body + '<p>取得完了時刻（UTC）: <code>' + escape(result['observed_at']) + '</code><br>' + (
        '保存結果です。追加の読取は行っていません。' if result.get('saved_result') else '取得時点のメタデータです。') + (
        '<br>実行 token: <code>' + escape(str(result.get('attempt_token', ''))) + '</code><br>証跡: <code>'
        + escape(str(result.get('evidence', ''))) + '</code></p>')


def render(result):
    try:
        return _render(result)
    except Exception:
        raise N.NotebookError('LAB_METADATA_INVALID') from None
