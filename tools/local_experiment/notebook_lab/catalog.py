"""Fixed Catalog/Lineage display through the existing owned read-only Reader."""
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
from tools.local_experiment.catalog_read import run as R

ROOT = Path(__file__).resolve().parents[3]
PROFILE = 'interactive-local-catalog-reader-v1'
ERROR_CODES = {'LAB_CATALOG_FAILED', 'LAB_CATALOG_SOURCE_CHANGED', 'INTERRUPTED'}
MAX_SUMMARY_BYTES = 512 * 1024


def require(value, code='LAB_CATALOG_INVALID'):
    N._require(value, code)


def normalize_request(value):
    try:
        value = N.CR.normalize_request(value)
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
    return N._catalog_reader_frames(raw, code, request['table'])


def _closed(output, request):
    return A._reader_closed(output / 'reader', request, lambda raw, code: _frames(raw, code, request),
                            command='catalog describe')


def saved_catalog(token, request, expected_seal=None):
    """Bind historical safe snapshots and bounded read receipts without HTTP."""
    require(type(token) is str and N.TOKEN.fullmatch(token))
    directory = R.STATE / token
    A.A._private_directory(directory)
    raw = N._read(directory / 'summary.json', MAX_SUMMARY_BYTES)
    summary = N._parse(raw)
    keys = {'schema_version', 'case', 'evidence_token', 'request', 'request_sha256', 'result',
            'started_at', 'finished_at', 'source_hashes', 'state_before', 'state_after',
            'runtime_before', 'runtime_after', 'snapshots', 'reads', 'observation', 'error_code',
            'cleanup_verified', 'invocations', 'retries', 'model_calls', 'sql_queries'}
    require(type(summary) is dict and set(summary) == keys
            and type(summary['schema_version']) is int and summary['schema_version'] == 1
            and summary['case'] == N.CR.PROFILE and summary['evidence_token'] == token
            and summary['request'] == request and summary['request_sha256'] == N.CR.digest(request)
            and summary['result'] == 'passed' and summary['error_code'] is None
            and summary['cleanup_verified'] is True)
    for key, expected in (('invocations', 1), ('retries', 0), ('model_calls', 0), ('sql_queries', 0)):
        require(type(summary[key]) is int and summary[key] == expected)
    for kind in ('state', 'runtime'):
        require(type(summary[kind + '_before']) is dict and summary[kind + '_before']
                and summary[kind + '_before'] == summary[kind + '_after'])
    hashes = summary['source_hashes']
    require(type(hashes) is dict and hashes
            and all(type(k) is str and type(v) is str and N.SHA.fullmatch(v) for k, v in hashes.items())
            and all(k in hashes for k in ('tools/local_experiment/catalog_read/run.py',
                                         'tools/local_experiment/catalog_read/contract.py')))
    for key in ('started_at', 'finished_at'):
        _timestamp(summary[key])
    require(datetime.fromisoformat(summary['started_at'][:-1] + '+00:00')
            <= datetime.fromisoformat(summary['finished_at'][:-1] + '+00:00'))
    snapshots = summary['snapshots']
    require(type(snapshots) is list and len(snapshots) == 2)
    public = N.CR.project_description(*snapshots, request)
    require(public == summary['observation'])
    expected_reads = [dict(method='POST', scope='login', attempt=1, result='finished', response_sha256=None)]
    for attempt, snapshot in enumerate(snapshots, 1):
        for key in ('team', 'service', 'database', 'schema', 'table', 'lineage'):
            expected_reads.append(dict(method='GET', scope=key, attempt=attempt, result='finished',
                                       response_sha256=snapshot['response_sha256'][key]))
    require(N._canonical(summary['reads']) == N._canonical(expected_reads))
    seal = {'summary_sha256': hashlib.sha256(raw).hexdigest()}
    require(expected_seal is None or seal == expected_seal)
    return public, seal


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
    require(value['schema_version'] == 6)
    request = value['input']
    stdout = sys.stdout if stdout is None else stdout
    source_factory = sources if source_factory is None else source_factory
    output = directory / 'catalog'; output.mkdir(mode=0o700)
    binary = output / 'shirokuma'
    original = None
    report = dict(schema_version=1, profile=PROFILE, request=request, request_sha256=N.C.digest(request),
                  result='running', error_code=None, model_calls=0, prepared=False, source_hashes=None,
                  binary_sha256=None, reader_started=False, reader_cleanup_confirmed=False,
                  catalog=None, observed_at=None, catalog_token=None, catalog_seal=None)

    def unchanged():
        require(Q._input(directory) == value, 'LAB_CATALOG_SOURCE_CHANGED')
        require(source_factory() == report['source_hashes'], 'LAB_CATALOG_SOURCE_CHANGED')
        if report['binary_sha256'] is not None:
            require(_binary_digest(binary) == report['binary_sha256'], 'LAB_CATALOG_SOURCE_CHANGED')

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
                public = (reader or N.read_catalog)(request['table'])
            catalog_token, verified, cleanup = _frames(N._read(read_dir / 'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request)
            require(cleanup and verified is not None and public == verified)
            require(_closed(output, request))
            saved, seal = saved_catalog(catalog_token, request)
            require(saved == verified)
            report['catalog_token'], report['catalog_seal'] = catalog_token, seal
            report['observed_at'] = A.stamp()
            report['catalog'] = verified
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
                str(original) if isinstance(original, N.NotebookError) and str(original) in ERROR_CODES else 'LAB_CATALOG_FAILED')
        if report['result'] != 'passed':
            report['catalog'] = report['observed_at'] = report['catalog_token'] = report['catalog_seal'] = None
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
        raise N.NotebookError('LAB_CATALOG_INVALID') from None
    return value


def terminal_receipt(directory, exit_code, *, verify_source=False):
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory)
    value = Q._input(directory); require(value['schema_version'] == 6)
    request = value['input']
    raw = N._read(directory / 'worker.stdout.json', 16384)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1)
    frame = N._parse(raw)
    output = directory / 'catalog'; Q._private_directory(output)
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
            'reader_cleanup_confirmed', 'catalog', 'observed_at', 'catalog_token', 'catalog_seal'}
            and type(report['schema_version']) is int and report['schema_version'] == 1 and report['profile'] == PROFILE
            and report['request'] == request and report['request_sha256'] == N.C.digest(request)
            and all(type(report[k]) is type(frame[k]) and report[k] == frame[k] for k in
                    ('result', 'error_code', 'model_calls', 'reader_cleanup_confirmed'))
            and all(type(report[k]) is bool for k in ('prepared', 'reader_started')))
    if report['prepared']:
        sealed = report['source_hashes']
        require(type(sealed) is dict and all(type(k) is str and type(v) is str and N.SHA.fullmatch(v)
                for k, v in sealed.items()) and all(key in sealed for key in
                ('tools/local_experiment/notebook_lab/catalog.py', 'tools/local_experiment/notebook_lab/query.py',
                 'tools/local_experiment/notebook/client.py', 'tools/local_experiment/catalog_read/contract.py'))
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
        catalog_token, public, cleanup = _frames(N._read(output / 'reader/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES * 2 + 2), 0, request)
        require(cleanup and public is not None and public == report['catalog']
                and catalog_token == report['catalog_token'])
        saved, _ = saved_catalog(catalog_token, request, report['catalog_seal'])
        require(saved == public)
        _timestamp(report['observed_at'])
    else:
        require(frame['error_code'] is not None and report['catalog'] is None and report['observed_at'] is None
                and report['catalog_token'] is None and report['catalog_seal'] is None)
    return directory.name, dict(schema_version=6, mode='catalog', command=request['command'], result=frame['result'],
        attempt_token=directory.name, evidence=evidence, catalog=report['catalog'], observed_at=report['observed_at'],
        model_calls=0, cleanup_verified=frame['reader_cleanup_confirmed'], catalog_token=report['catalog_token'])


def recover(attempt_token):
    from tools.local_experiment.notebook_lab import query as Q
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token))
        directory = Q._directory(Q.STATE / attempt_token)
        require(Q._input(directory)['schema_version'] == 6)
        status = Q.query_status(directory)
        require(status is not None and status['state'] == 'passed' and status['child_exited'] and status['cleanup_verified'])
        token, public = terminal_receipt(directory, status['exit_code'])
        require(token == status['evidence_token'] and public['result'] == 'passed' and public['cleanup_verified']
                and Q._json(directory / 'result.json', N.C.MAX_PUBLIC_BYTES) == public)
        return dict(public, saved_result=True)
    except Exception:
        raise N.NotebookError('LAB_CATALOG_INVALID') from None


def render(result):
    """Render the safe public projection; no paths, credentials or live reads."""
    try:
        keys = {'schema_version', 'mode', 'command', 'result', 'attempt_token', 'evidence',
                'catalog', 'observed_at', 'model_calls', 'cleanup_verified', 'catalog_token'}
        require(type(result) is dict and set(result) in (keys, keys | {'saved_result'})
                and type(result['schema_version']) is int and result['schema_version'] == 6
                and result['mode'] == 'catalog' and result['command'] == 'describe'
                and result['result'] == 'passed' and result['cleanup_verified'] is True
                and type(result['model_calls']) is int and result['model_calls'] == 0
                and type(result.get('saved_result', False)) is bool
                and type(result['attempt_token']) is str and N.TOKEN.fullmatch(result['attempt_token'])
                and type(result['catalog_token']) is str and N.TOKEN.fullmatch(result['catalog_token']))
        _timestamp(result['observed_at'])
        public = N._catalog_public(result['catalog'], N.CR.TABLE)
        def text(value):
            return '未記録' if value is None else '空文字' if value == '' else escape(value)
        owners = '、'.join(escape(owner['name']) for owner in public['owners'])
        body = '<h3>Catalog: ' + escape(public['table']) + '</h3><p>所有者: ' + owners + '</p>'
        body += '<p>説明: ' + text(public['description']) + '</p>'
        body += '<h4>列</h4><table><thead><tr><th>名前</th><th>型</th><th>説明</th></tr></thead><tbody>'
        body += ''.join('<tr><td>' + escape(c['name']) + '</td><td>' + escape(c['type']) +
                        '</td><td>' + text(c['comment']) + '</td></tr>' for c in public['columns'])
        body += '</tbody></table><h4>記録されたLineage（1 hop）</h4>'
        nodes = {public['catalog_entity']['id']: public['catalog_entity']['fully_qualified_name']}
        nodes.update({node['id']: node['fully_qualified_name'] for node in public['lineage']['nodes']})
        edges = public['lineage']['edges']
        if edges:
            body += '<table style="table-layout:fixed;width:100%;overflow-wrap:anywhere"><thead><tr><th>元</th><th>先</th><th>記録元</th></tr></thead><tbody>'
            body += ''.join('<tr><td>' + escape(nodes[e['from_entity']]) + '</td><td>' +
                            escape(nodes[e['to_entity']]) + '</td><td>' + text(e['source']) +
                            '</td></tr>' for e in edges)
            body += '</tbody></table>'
        else:
            body += '<p>記録された関連は0件です。</p>'
        body += '<p>Catalogに保存されたメタデータです。過去の関連を含む場合があります。' + (
            '現在のSQLテーブルの実在や処理の実行を確認した結果ではありません。</p>')
        body += '<p>Catalog version: <code>' + escape(public['catalog_entity']['version']) + (
            '</code><br>Catalog更新時刻（Unix ms）: <code>') + str(public['catalog_entity']['updated_at_ms']) + (
            '</code><br>取得完了時刻（UTC）: <code>') + escape(result['observed_at']) + '</code></p>'
        body += '<p>' + ('保存結果です。追加GETは行っていません。' if result.get('saved_result') else
                         '取得時点の保存情報です。') + '<br>実行token: <code>' + escape(result['attempt_token']) + '</code></p>'
        return body
    except Exception:
        raise N.NotebookError('LAB_CATALOG_INVALID') from None
