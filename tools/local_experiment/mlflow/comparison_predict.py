#!/usr/bin/env python3
"""Predict from one saved exploratory Ridge candidate without refitting it."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import predict as P
from tools.local_experiment.notebook_lab import comparison_saved as S

PROFILE = 'saved-comparison-prediction-v1'
SCOPE = '保存済みの探索的 Ridge 比較モデルによる予測です。再学習や MLflow 登録は行わず、新入力での正解精度を示すものではありません。'
ERROR_CODES = {'COMPARISON_PREDICTION_ARGUMENTS_INVALID', 'COMPARISON_PREDICTION_REQUEST_INVALID',
               'COMPARISON_PREDICTION_SOURCE_INVALID', 'COMPARISON_PREDICTION_NUMBER_INVALID',
               'COMPARISON_PREDICTION_INTERRUPTED'}
PUBLIC_KEYS = {'schema_version', 'profile', 'result', 'comparison_token', 'comparison_sha256',
               'comparison_request_sha256', 'request_sha256', 'identity', 'lake_source',
               'alpha', 'model_sha256', 'predictions', 'error_code', 'scope'}
canonical, digest, MAX_INPUT_BYTES = P.canonical, P.digest, P.MAX_INPUT_BYTES
MAX_PUBLIC_BYTES = 32 * 1024
load_saved = S.load_saved


class ComparisonPredictionError(ValueError):
    """Only fixed reason codes cross the public prediction boundary."""


def require(condition, code):
    if not condition:
        raise ComparisonPredictionError(code)


def normalize_request(value):
    try:
        return P.normalize_request(value)
    except Exception:
        raise ComparisonPredictionError('COMPARISON_PREDICTION_REQUEST_INVALID') from None


def parse_request(raw):
    try:
        return P.parse_request(raw)
    except Exception:
        raise ComparisonPredictionError('COMPARISON_PREDICTION_REQUEST_INVALID') from None


def predict(comparison_token, request):
    require(type(comparison_token) is str and P.TOKEN.fullmatch(comparison_token),
            'COMPARISON_PREDICTION_ARGUMENTS_INVALID')
    request = normalize_request(request)
    try:
        saved = load_saved(comparison_token)
    except Exception:
        raise ComparisonPredictionError('COMPARISON_PREDICTION_SOURCE_INVALID') from None
    comparison = saved['comparison']
    candidate = comparison['candidate']
    model = dict(algorithm=candidate['algorithm'], alpha=candidate['alpha'], solver=candidate['solver'],
                 fit_intercept=candidate['fit_intercept'], feature_names=list(P.FEATURES), **deepcopy(candidate['model']))
    predictions = []
    for row in request['rows']:
        try:
            value = math.fsum([model['intercept'], *(coefficient * row[feature]
                              for coefficient, feature in zip(model['coefficients'], P.FEATURES))])
            require(math.isfinite(value), 'COMPARISON_PREDICTION_NUMBER_INVALID')
        except (ValueError, OverflowError):
            raise ComparisonPredictionError('COMPARISON_PREDICTION_NUMBER_INVALID') from None
        predictions.append(value)
    public = dict(schema_version=1, profile=PROFILE, result='passed', comparison_token=comparison_token,
                  comparison_sha256=digest(comparison), comparison_request_sha256=comparison['request_sha256'],
                  request_sha256=digest(request), identity=deepcopy(comparison['identity']),
                  lake_source=deepcopy(comparison['lake_source']), alpha=candidate['alpha'],
                  model_sha256=digest(model), predictions=predictions, error_code=None, scope=SCOPE)
    require(len(canonical(public)) + 1 <= MAX_PUBLIC_BYTES, 'COMPARISON_PREDICTION_NUMBER_INVALID')
    return public


def failure(code):
    require(code in ERROR_CODES, 'COMPARISON_PREDICTION_SOURCE_INVALID')
    return dict.fromkeys(PUBLIC_KEYS) | dict(schema_version=1, profile=PROFILE, result='failed', scope=SCOPE, error_code=code)


def main(argv=None, *, stdin=None, stdout=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    first = None
    try:
        args = sys.argv[1:] if argv is None else argv
        require(type(args) is list and len(args) == 3 and args[0] == '--comparison-token'
                and type(args[1]) is str and P.TOKEN.fullmatch(args[1]) and args[2] == '--request-stdin',
                'COMPARISON_PREDICTION_ARGUMENTS_INVALID')
        public = predict(args[1], parse_request(stdin.read(MAX_INPUT_BYTES + 1)))
    except Exception as error:
        code = str(error) if type(error) is ComparisonPredictionError and str(error) in ERROR_CODES else 'COMPARISON_PREDICTION_SOURCE_INVALID'
        public = failure(code)
    except BaseException as error:
        first, public = error, failure('COMPARISON_PREDICTION_INTERRUPTED')
    try:
        stdout.write(canonical(public).decode('ascii') + '\n')
        stdout.flush()
    except BaseException as error:
        first = S.T._keep(first, error)
    if first is not None:
        if not isinstance(first, Exception):
            raise first
        return 1
    return 0 if public['result'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
