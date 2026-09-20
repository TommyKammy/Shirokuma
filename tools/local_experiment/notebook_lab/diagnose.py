"""Owned Lab access to the existing Query Doctor and its saved evidence."""
from __future__ import annotations

import hashlib
from html import escape
import os
from pathlib import Path
import sys

from tools.local_experiment.notebook import client as N
from tools.local_experiment.notebook_assist import run as A
from tools.local_experiment.notebook_lab import metadata as M
from tools.local_experiment.query_diagnose import contract as C, run as R
from tools.local_experiment.query_diagnose_report import contract as REPORT

ROOT = Path(__file__).resolve().parents[3]
PROFILE = 'interactive-local-query-diagnose-v1'
ERROR_CODES = {'LAB_DIAGNOSE_FAILED', 'LAB_DIAGNOSE_SOURCE_CHANGED', 'INTERRUPTED'}


def require(value, code='LAB_DIAGNOSE_INVALID'):
    N._require(value, code)


def normalize_request(value):
    try:
        require(type(value) is dict and set(value) == {'read_token'}
                and type(value['read_token']) is str and N.TOKEN.fullmatch(value['read_token']))
        return dict(value)
    except Exception:
        raise N.NotebookError('LAB_QUERY_INVALID') from None


def sources():
    value = R.sources()
    paths = [Path(__file__), Path(__file__).with_name('query.py'), Path(N.__file__), Path(A.__file__),
             Path(M.__file__), Path(REPORT.__file__), ROOT/'go.mod', ROOT/'go.sum',
             *sorted((ROOT/'internal/cli').glob('*.go')), *sorted((ROOT/'cmd/shirokuma').glob('*.go'))]
    value.update({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    return value


def _frames(raw, code, request):
    return N._diagnose_reader_frames(raw, code, request['read_token'])


def _closed(output, request):
    return A._reader_closed(output/'reader', N._diagnose_request(request['read_token']),
                            lambda raw, code: _frames(raw, code, request))


def saved_diagnosis(token, read_token, expected_seal=None):
    """Validate historical summary/spans, never reopen the source SELECT or model."""
    require(type(token) is str and N.TOKEN.fullmatch(token) and token != read_token)
    directory = R.STATE/token
    A.A._private_directory(directory)
    summary_raw = N._read(directory/'summary.json', REPORT.MAX_SUMMARY_BYTES)
    spans_raw = N._read(directory/'spans.jsonl', REPORT.MAX_SPANS_BYTES)
    summary = N._parse(summary_raw)
    spans = [N._parse(line) for line in spans_raw.splitlines() if line]
    entry = REPORT.project_run(token, summary, spans)
    require(entry['state'] == 'passed' and entry['verification'] == 'saved-evidence'
            and entry['cleanup_verified'] is True and entry['read_token'] == read_token)
    public = C.public_summary(summary['source'], summary['model_evidence'], summary['diagnosis'])
    N._diagnose_public(public, read_token)
    seal = dict(summary_sha256=hashlib.sha256(summary_raw).hexdigest(), spans_sha256=hashlib.sha256(spans_raw).hexdigest())
    require(expected_seal is None or seal == expected_seal)
    return public, seal


def worker(directory, *, build=None, reader=None, source_factory=None, stdout=None):
    """Run within the existing Lab worker; the client owns the one Doctor child."""
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory); value = Q._input(directory)
    require(value['schema_version'] == 5); request = value['input']
    stdout = sys.stdout if stdout is None else stdout
    source_factory = sources if source_factory is None else source_factory
    output = directory/'diagnose'; output.mkdir(mode=0o700); binary = output/'shirokuma'
    report = dict(schema_version=1, profile=PROFILE, request=request, request_sha256=N.C.digest(request),
        result='running', error_code=None, model_calls=0, prepared=False, source_hashes=None,
        binary_sha256=None, reader_started=False, reader_cleanup_confirmed=False,
        diagnosis_token=None, diagnosis=None, diagnosis_seal=None, observed_at=None)
    original = None

    def unchanged():
        require(Q._input(directory) == value and source_factory() == report['source_hashes'], 'LAB_DIAGNOSE_SOURCE_CHANGED')
        if report['binary_sha256'] is not None:
            require(M._binary_digest(binary) == report['binary_sha256'], 'LAB_DIAGNOSE_SOURCE_CHANGED')

    try:
        with A.A._signals():
            report['source_hashes'] = source_factory()
            N._write(output/'execution-intent.json', dict(schema_version=1, profile=PROFILE,
                request_sha256=report['request_sha256'], source_hashes=report['source_hashes']))
            report['prepared'] = True
            with (output/'build.log').open('xb') as log:
                os.fchmod(log.fileno(), 0o600); (build or M._build)(binary, log)
            report['binary_sha256'] = M._binary_digest(binary); unchanged()
            read_dir = output/'reader'; read_dir.mkdir(mode=0o700)
            report['reader_started'] = True; report['model_calls'] = None
            with A.reader_environment(read_dir, binary):
                public = (reader or N.read_diagnose)(request['read_token'])
            token, verified, cleanup = _frames(N._read(read_dir/'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES*2+2), 0, request)
            require(cleanup and public is not None and public == verified and _closed(output, request))
            original_public, seal = saved_diagnosis(token, request['read_token'])
            require(original_public == public)
            report.update(diagnosis_token=token, diagnosis=public, diagnosis_seal=seal,
                          model_calls=1, observed_at=A.stamp())
            unchanged()
    except BaseException as error:
        original = error
    finally:
        try:
            if report['prepared']: unchanged()
        except BaseException as error:
            original = A.A._keep(original, error)
        try:
            report['reader_cleanup_confirmed'] = _closed(output, request)
        except BaseException as error:
            original = A.A._keep(original, error)
        report['result'] = ('unknown' if not report['reader_cleanup_confirmed'] else 'passed' if original is None
                            else 'failed' if isinstance(original, Exception) else 'interrupted')
        if original is not None:
            report['error_code'] = ('INTERRUPTED' if not isinstance(original, Exception) else str(original)
                if isinstance(original, N.NotebookError) and str(original) in ERROR_CODES else 'LAB_DIAGNOSE_FAILED')
        if report['result'] != 'passed': report['diagnosis'] = report['observed_at'] = None
        try:
            N._write(output/'owner-result.json', report)
        except BaseException as error:
            raise A.A._keep(original, error)
    frame = {key: report[key] for key in ('result','error_code','model_calls','reader_cleanup_confirmed')}
    frame['evidence'] = str((output/'owner-result.json').relative_to(ROOT))
    stdout.write(N._canonical(frame).decode('ascii'))
    if original is not None and not isinstance(original, Exception):
        return 128 + original.signum if isinstance(original, A.A.AssetsInterrupted) else 130
    return 0 if report['result'] == 'passed' else 1


def terminal_receipt(directory, exit_code, *, verify_source=False):
    from tools.local_experiment.notebook_lab import query as Q
    directory = Q._directory(directory); value = Q._input(directory); require(value['schema_version'] == 5)
    request = value['input']; output = directory/'diagnose'; Q._private_directory(output)
    raw = N._read(directory/'worker.stdout.json', 16384)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1); frame = N._parse(raw)
    evidence = str((output/'owner-result.json').relative_to(ROOT))
    require(type(frame) is dict and set(frame) == {'result','error_code','model_calls','reader_cleanup_confirmed','evidence'}
        and frame['evidence'] == evidence and frame['result'] in ('passed','failed','interrupted','unknown')
        and (frame['error_code'] is None or frame['error_code'] in ERROR_CODES)
        and (frame['model_calls'] is None or type(frame['model_calls']) is int and frame['model_calls'] in (0,1))
        and type(frame['reader_cleanup_confirmed']) is bool and type(exit_code) is int
        and exit_code in {'passed':(0,), 'failed':(1,), 'interrupted':(130,143),'unknown':(1,130,143)}[frame['result']])
    report = Q._json(output/'owner-result.json', N.C.MAX_PUBLIC_BYTES)
    require(type(report) is dict and set(report) == {'schema_version','profile','request','request_sha256','result',
        'error_code','model_calls','prepared','source_hashes','binary_sha256','reader_started','reader_cleanup_confirmed',
        'diagnosis_token','diagnosis','diagnosis_seal','observed_at'}
        and type(report['schema_version']) is int and report['schema_version'] == 1 and report['profile'] == PROFILE
        and report['request'] == request and report['request_sha256'] == N.C.digest(request)
        and all(type(report[k]) is type(frame[k]) and report[k] == frame[k] for k in
                ('result','error_code','model_calls','reader_cleanup_confirmed'))
        and type(report['prepared']) is bool and type(report['reader_started']) is bool)
    if report['prepared']:
        sealed = report['source_hashes']
        require(type(sealed) is dict and all(type(k) is str and type(v) is str and N.SHA.fullmatch(v) for k,v in sealed.items())
            and all(k in sealed for k in ('tools/local_experiment/notebook_lab/diagnose.py',
                'tools/local_experiment/notebook_lab/query.py','tools/local_experiment/notebook/client.py',
                'tools/local_experiment/query_diagnose/contract.py','tools/local_experiment/query_diagnose_report/contract.py'))
            and Q._json(output/'execution-intent.json', N.C.MAX_PUBLIC_BYTES) == dict(schema_version=1,
                profile=PROFILE, request_sha256=report['request_sha256'], source_hashes=sealed))
        if verify_source and frame['result'] == 'passed': require(sealed == sources())
    else:
        require(frame['result'] in ('failed','interrupted') and not report['reader_started']
                and not (output/'execution-intent.json').exists())
    if report['reader_started']:
        require(report['prepared'] and type(report['binary_sha256']) is str and N.SHA.fullmatch(report['binary_sha256']))
    else:
        require(report['model_calls'] == 0 and not (output/'reader').exists() and not (output/'reader').is_symlink())
    require(not frame['reader_cleanup_confirmed'] or _closed(output, request))
    if frame['result'] == 'passed':
        require(frame['error_code'] is None and frame['reader_cleanup_confirmed'] and report['reader_started']
                and report['model_calls'] == 1 and type(report['diagnosis_seal']) is dict
                and set(report['diagnosis_seal']) == {'summary_sha256','spans_sha256'}
                and all(type(v) is str and N.SHA.fullmatch(v) for v in report['diagnosis_seal'].values()))
        token, public, cleanup = _frames(N._read(output/'reader/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES*2+2), 0, request)
        require(cleanup and token == report['diagnosis_token'] and public == report['diagnosis'])
        saved, _ = saved_diagnosis(token, request['read_token'], report['diagnosis_seal'])
        require(saved == public); M._timestamp(report['observed_at'])
    else:
        require(frame['error_code'] is not None and report['diagnosis'] is None and report['observed_at'] is None)
    return directory.name, dict(schema_version=5, mode='diagnose', result=frame['result'], attempt_token=directory.name,
        evidence=evidence, diagnosis=report['diagnosis'], diagnosis_token=report['diagnosis_token'],
        observed_at=report['observed_at'], model_calls=report['model_calls'], cleanup_verified=frame['reader_cleanup_confirmed'])


def recover(attempt_token):
    from tools.local_experiment.notebook_lab import query as Q
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token))
        directory = Q._directory(Q.STATE/attempt_token); require(Q._input(directory)['schema_version'] == 5)
        status = Q.query_status(directory)
        require(status is not None and status['state'] == 'passed' and status['child_exited'] and status['cleanup_verified'])
        token, public = terminal_receipt(directory, status['exit_code'])
        require(token == status['evidence_token'] and public['result'] == 'passed' and public['cleanup_verified']
                and Q._json(directory/'result.json', N.C.MAX_PUBLIC_BYTES) == public)
        return dict(public, saved_result=True)
    except Exception:
        raise N.NotebookError('LAB_DIAGNOSE_INVALID') from None


def preview_read_token(attempt_token):
    from tools.local_experiment.notebook_lab import query as Q
    try:
        require(type(attempt_token) is str and N.TOKEN.fullmatch(attempt_token))
        directory = Q._directory(Q.STATE/attempt_token); value = Q._input(directory)
        require(value['schema_version'] == 1)
        status = Q.query_status(directory)
        require(status is not None and status['state'] == 'passed' and status['child_exited'] and status['cleanup_verified'])
        assist_token, public = Q.terminal_receipt(directory, status['exit_code'])
        require(assist_token == status['evidence_token'] and public['result'] == 'passed' and public['preview'] is not None
                and Q._json(directory/'result.json', N.C.MAX_PUBLIC_BYTES) == public)
        output = (A.FILTERED_STATE if value['filtered'] else A.STATE)/assist_token
        summary = Q._json(output/'summary.json', N.C.MAX_PUBLIC_BYTES)
        reader = N._filtered_reader_frames if value['filtered'] else N._reader_frames
        token, observed, cleanup = reader(N._read(output/'assets/reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES*2+2),
                                          0, request=summary['selection'])
        require(cleanup and observed == public['preview'] and token not in (attempt_token, assist_token))
        return token
    except Exception:
        raise N.NotebookError('LAB_DIAGNOSE_PREVIEW_INVALID') from None


FINDINGS = {'RESULT_LIMIT_REACHED':'プレビューの上限に達しています',
    'HISTORY_NOT_RETAINED':'実行履歴が保持されていません','HISTORY_TIMING_INCOMPLETE':'実行履歴の時間情報が一部不明です',
    'QUEUE_TIME_OBSERVED':'待ち時間が観測されています','PLAN_ESTIMATES_INCOMPLETE':'実行計画の推定値が一部不明です',
    'TABLE_STATISTICS_INCOMPLETE':'テーブル統計が一部不明です'}
STEPS = {'REVIEW_READER_LIMIT':'プレビューの上限を確認する','CAPTURE_HISTORY_PROMPTLY':'実行履歴を早めに確認する',
    'CHECK_HISTORY_TIMING':'実行履歴の時間情報を確認する','REVIEW_QUEUE_TIMING':'待ち時間を確認する',
    'REVIEW_TABLE_STATISTICS':'テーブル統計を確認する','MEASURE_BEFORE_OPTIMIZING':'最適化の前に実測する'}


def render(result):
    try:
        require(type(result) is dict and type(result.get('schema_version')) is int and result['schema_version'] == 5
            and result.get('mode') == 'diagnose' and result.get('result') == 'passed'
            and result.get('cleanup_verified') is True and type(result.get('model_calls')) is int and result['model_calls'] == 1
            and type(result.get('saved_result', False)) is bool)
        public = result['diagnosis']; N._diagnose_public(public, public['read_token']); M._timestamp(result['observed_at'])
        list_html = lambda values, labels: '<ul>' + ''.join('<li>'+escape(labels[v])+' <code>'+escape(v)+'</code></li>' for v in values)+'</ul>'
        return '<section><h3>Query Doctor</h3><p>元の SELECT は再実行していません。</p><h4>観測できたこと</h4>' + (
            list_html(public['findings'], FINDINGS) if public['findings'] else '<p>この限定観測で該当する所見はありません。</p>') + (
            '<h4>次に確認すること</h4>' + list_html(public['next_steps'], STEPS) +
            '<p>根本原因は未確定です。改善効果は実測していません。推定値は実使用量ではなく、履歴は永続監査ではありません。</p>'
            '<details><summary>診断の出典</summary><p>SELECT token: <code>' + escape(public['read_token']) +
            '</code><br>query ID: <code>' + escape(public['query_id']) + '</code><br>履歴: <code>' + escape(public['history']) +
            '</code><br>Lab 実行 token: <code>' + escape(str(result.get('attempt_token',''))) +
            '</code><br>取得完了（UTC）: <code>' + escape(result['observed_at']) + '</code></p></details><p>' +
            ('保存結果です。追加の診断・モデル呼出は行っていません。' if result.get('saved_result') else '取得時点の限定診断です。') + '</p></section>')
    except Exception:
        raise N.NotebookError('LAB_DIAGNOSE_INVALID') from None
