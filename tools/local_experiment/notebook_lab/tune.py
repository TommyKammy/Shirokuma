"""Fixed train-only Ridge CV through the existing owned ML worker protocol."""
from __future__ import annotations

from html import escape
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import tune as C
from tools.local_experiment.notebook_lab import compare as B, comparison_saved as S

N, T = B.N, B.T
LAB_STATE = B.LAB_STATE
STATE = LAB_STATE / 'tunings'
PROFILE = 'interactive-ridge-cv-v1'
FILES = dict(S.FILES, **{'input.json': C.MAX_INPUT_BYTES,
                        'worker.stdout.json': C.MAX_PUBLIC_BYTES,
                        'result.json': C.MAX_PUBLIC_BYTES + 4096})


def require(condition, code='LAB_TUNING_INVALID'):
    N._require(condition, code)


def arguments(directory):
    return [str(B.PYTHON), '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)]


def _input(directory):
    raw = N._read(directory / 'input.json', C.MAX_INPUT_BYTES)
    value = C.parse_request(raw)
    require(raw == N._canonical(value))
    return value


def _saved(token):
    return S.load_saved(token, state=STATE, lab_state=LAB_STATE, file_limits=FILES,
        validator=lambda raw, selected, directory: S._validate(raw, selected, directory,
            core=C, profile=PROFILE, arguments_fn=arguments))


def tuning_status(directory):
    return B.comparison_status(directory, state=STATE)


def tuning_closed(directory):
    value = tuning_status(directory)
    if value is None or not value['child_exited'] or not value['cleanup_verified']:
        return False
    if value['state'] == 'passed':
        # A completed saved receipt binds its launch and terminal state. A PID
        # reused today does not make that historical worker active again.
        _saved(directory.name)
        return True
    return value['pid'] is None or T.group_empty(value['pid'])


def check_idle():
    """Read-only residual check; all callers hold the shared Lab owner lock."""
    require(all(not path.is_symlink() for path in (STATE, *STATE.parents)))
    if not STATE.exists():
        return True
    B._private(STATE)
    entries = list(STATE.iterdir())
    require(len(entries) <= B.MAX_ATTEMPTS)
    for directory in entries:
        require(N.TOKEN.fullmatch(directory.name))
        if not tuning_closed(directory):
            return False
    return True


def _result(directory, request, exit_code):
    require(type(exit_code) is int and exit_code == 0, 'LAB_TUNING_FAILED')
    raw = N._read(directory / 'worker.stdout.json', C.MAX_PUBLIC_BYTES)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1 and _input(directory) == request)
    value = C.validate_result(N._parse(raw), request)
    require(raw == N._canonical(value))
    return dict(schema_version=1, profile=PROFILE, result='passed', attempt_token=directory.name,
                cleanup_verified=True, comparison=value)


def tune(training_token):
    """One explicit attempt: nine train-only CV fits and one selected refit."""
    try:
        request = C.normalize_request(dict(schema_version=1, training_token=training_token))
        return B._execute(request, state=STATE, profile=PROFILE, arguments_fn=arguments,
                          environment_fn=B.environment, result_fn=_result, lock_fn=B._lock,
                          request_digest=C.digest(request))
    except N.NotebookError as error:
        code = str(error).replace('LAB_COMPARISON_', 'LAB_TUNING_', 1)
        raise N.NotebookError(code) from None
    except Exception:
        raise N.NotebookError('LAB_TUNING_FAILED') from None


def recover_tuning(attempt_token):
    """Reconstruct a verified saved result; no process, model fit, or service."""
    try:
        return dict(_saved(attempt_token), saved_result=True)
    except Exception:
        raise N.NotebookError('LAB_TUNING_SAVED_INVALID') from None


def worker(directory, *, calculate=None):
    directory = B._directory(directory, state=STATE)
    request = _input(directory)
    require(B._json(directory / 'intent.json') == dict(schema_version=1, profile=PROFILE,
            argv=arguments(directory), request_sha256=C.digest(request)))
    deadline = time.monotonic() + 30
    while not (directory / 'launch.json').exists():
        require(time.monotonic() < deadline)
        time.sleep(.05)
    status = tuning_status(directory)
    require(status is not None and status['state'] == 'running' and status['pid'] == status['pgid'] == os.getpid()
            and os.getpgrp() == os.getpid() and status['process_start'] == N._process_start(os.getpid())
            and B._json(directory / 'launch.json') == dict(pid=os.getpid(), ppid=os.getppid(), pgid=os.getpid(),
                 process_start=status['process_start']))
    return (calculate or C.main)(['--training-token', request['training_token']])


def render_tuning(result):
    """Compact escaped CV results; no further calculation or external reads."""
    require(type(result) is dict and result.get('profile') == PROFILE and result.get('result') == 'passed'
            and result.get('cleanup_verified') is True and type(result.get('comparison')) is dict)
    value = result['comparison']
    require(value.get('profile') == C.PROFILE)
    text = lambda x: escape(str(x))
    number = lambda x: text(format(B.C._number(x), '.6g'))
    cv = value['cv']
    rows = ''
    for candidate in cv['candidates']:
        cells = [number(candidate['alpha'])]
        cells += [number(fold['validation_metrics']['mse']) for fold in candidate['folds']]
        cells += [number(candidate['mean_validation_mse']),
                  '採用' if candidate['alpha'] == cv['selected_alpha'] else '']
        rows += '<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>'
    holdout = ''
    for key, label in (('reference', '保存モデル'), ('candidate', '選択後の再学習モデル')):
        metrics = value[key]['metrics']['test']
        holdout += '<tr><td>' + label + '</td><td>' + number(value[key]['alpha']) + '</td>'
        holdout += ''.join('<td>' + number(metrics[k]) + '</td>' for k in ('rmse', 'mae', 'r2')) + '</tr>'
    saved = '<p>保存済み結果の再表示です。追加の学習は実行していません。</p>' if result.get('saved_result') is True else ''
    return ('<section><h3>学習データ内での Ridge 条件選択</h3>' + saved +
        '<p>48行の学習データを固定の3分割で検証しました。平均 validation MSE が最小の alpha を採用し、'
        '同点なら小さい alpha を選びます。選択後に48行すべてで1回再学習します。</p>' +
        '<p>選択 alpha: <strong>' + number(cv['selected_alpha']) + '</strong> / seed: ' + text(cv['seed']) +
        ' / 学習回数: ' + text(cv['fit_count']) + '（9回の検証と1回の再学習）</p>' +
        '<table><thead><tr><th>alpha</th><th>fold 1 MSE</th><th>fold 2 MSE</th><th>fold 3 MSE</th>'
        '<th>平均 MSE</th><th>選択</th></tr></thead><tbody>' + rows + '</tbody></table>' +
        '<h4>選択後の参考評価</h4><p>既に閲覧した holdout 16行を再利用しています。選択には使っていませんが、'
        '独立した最終テストではありません。</p><table><thead><tr><th>モデル</th><th>alpha</th>'
        '<th>RMSE</th><th>MAE</th><th>R²</th></tr></thead><tbody>' + holdout + '</tbody></table>' +
        '<p>MLflow への登録やモデルの昇格は行っていません。</p><details><summary>保存元と実行記録</summary>'
        '<p>元の学習: <code>' + text(value['identity']['attempt_token']) + '</code><br>CV 実行: <code>' +
        text(result['attempt_token']) + '</code></p></details></section>')


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
