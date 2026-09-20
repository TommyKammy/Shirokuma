#!/usr/bin/env python3
"""One offline Ridge fit using a verified saved Iceberg dataset and split.

The candidate is exploratory: its holdout has already been seen. No SDK,
Tracking write, Reader, executable model loading or output file is used here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import training as T

PROFILE = 'saved-ridge-comparison-v1'
MAX_INPUT_BYTES, MAX_PUBLIC_BYTES = 4096, 32 * 1024
SCOPE = '保存済みの同じ48行で学習し、同じ16行を再利用した探索的比較です。独立した最終評価ではありません。'
ERROR_CODES = {'COMPARISON_ARGUMENTS_INVALID', 'COMPARISON_REQUEST_INVALID',
               'COMPARISON_SOURCE_INVALID', 'COMPARISON_MODEL_INVALID',
               'COMPARISON_FIT_FAILED', 'COMPARISON_INTERRUPTED'}
PUBLIC_KEYS = {'schema_version', 'profile', 'result', 'request_sha256', 'identity',
               'lake_source', 'dataset_sha256', 'split_sha256', 'source_model_sha256',
               'reference', 'candidate', 'metric_deltas', 'holdout', 'versions', 'scope', 'error_code'}


class ComparisonError(ValueError):
    """Only fixed reason codes cross the CLI boundary."""


def require(condition, code='COMPARISON_MODEL_INVALID'):
    if not condition:
        raise ComparisonError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def normalize_request(value):
    code = 'COMPARISON_REQUEST_INVALID'
    require(type(value) is dict and set(value) == {'schema_version', 'training_token', 'alpha'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['training_token']) is str
            and re.fullmatch(r'[0-9a-f]{32}', value['training_token']) is not None
            and type(value['alpha']) in (int, float), code)
    try:
        alpha = float(value['alpha'])
        require(math.isfinite(alpha) and .001 <= alpha <= 1000, code)
    except (ValueError, OverflowError):
        raise ComparisonError(code) from None
    return dict(schema_version=1, training_token=value['training_token'], alpha=alpha)


def parse_request(raw):
    code = 'COMPARISON_REQUEST_INVALID'
    require(type(raw) is bytes and 0 < len(raw) <= MAX_INPUT_BYTES, code)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, code)
            result[key] = value
        return result
    try:
        return normalize_request(json.loads(raw, object_pairs_hook=unique,
                                 parse_constant=lambda _: require(False, code)))
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ComparisonError(code) from None


def _saved(request, operation):
    """Use the existing cold receipt/workload/artifact checks without HTTP.

    Recheck the same private bytes and file identities after the calculation,
    including failure paths, while retaining the first cancellation.
    """
    saved, first, result = {}, None, None
    try:
        receipt, workload, _, saved = T._sources(request['training_token'], kind='iceberg')
        reference = T._project(receipt, workload, kind='iceberg')
        result = operation(workload, reference)
    except BaseException as error:
        first = error
    finally:
        first = T._recheck(saved, first)
    if first is not None:
        if not isinstance(first, Exception) or isinstance(first, ComparisonError):
            raise first
        raise ComparisonError('COMPARISON_SOURCE_INVALID') from None
    return result


def _number(value):
    require(type(value) in (int, float))
    try:
        require(math.isfinite(value))
        return float(value)
    except OverflowError:
        raise ComparisonError('COMPARISON_MODEL_INVALID') from None


def _model(value, train, alpha):
    require(type(value) is dict and set(value) == {'coefficients', 'intercept'}
            and type(value['coefficients']) is list and len(value['coefficients']) == 3)
    coefficients = [_number(x) for x in value['coefficients']]
    intercept = _number(value['intercept'])
    means = [math.fsum(row[j] for row in train) / 48 for j in range(4)]
    require(math.isclose(intercept, means[3] - math.fsum(coefficients[j] * means[j] for j in range(3)),
                         rel_tol=1e-10, abs_tol=1e-8))
    for j in range(3):
        gradient = math.fsum((row[j] - means[j]) * (
            math.fsum((row[k] - means[k]) * coefficients[k] for k in range(3)) -
            (row[3] - means[3])) for row in train) + alpha * coefficients[j]
        require(math.isfinite(gradient) and abs(gradient) <= 1e-7)
    return dict(coefficients=coefficients, intercept=intercept)


def _predict(rows, model):
    return [_number(math.fsum([model['intercept'], *(row[j] * model['coefficients'][j]
                    for j in range(3))])) for row in rows]


def _fit(train, alpha):
    """Only the train partition enters sklearn, including intercept centering."""
    import numpy as np
    import sklearn
    from sklearn.linear_model import Ridge
    rows = np.asarray(train, dtype=np.float64)
    estimator = Ridge(alpha=alpha, solver='svd', fit_intercept=True)
    estimator.fit(rows[:, :3], rows[:, 3])
    return (dict(coefficients=estimator.coef_.tolist(), intercept=float(estimator.intercept_)),
            dict(python='.'.join(str(x) for x in sys.version_info[:3]),
                 numpy=np.__version__, scikit_learn=sklearn.__version__))


def _project(request, workload, reference, model, versions):
    require(type(versions) is dict and set(versions) == {'python', 'numpy', 'scikit_learn'}
            and all(type(v) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,32}', v)
                    for v in versions.values()))
    rows, split = workload['dataset']['rows'], workload['split']
    train = [rows[i] for i in split['train_indices']]
    test = [rows[i] for i in split['test_indices']]
    model = _model(model, train, request['alpha'])
    predictions = _predict(test, model)
    metrics = {'train': T._metrics([row[3] for row in train], _predict(train, model)),
               'test': T._metrics([row[3] for row in test], predictions)}
    reference_metrics = {key: deepcopy(reference['metrics'][key]) for key in ('train', 'test')}
    for group in metrics.values():
        for number in group.values():
            _number(number)
    differences = [{key: _number(metrics[group][key] - reference_metrics[group][key])
                    for key in ('mae', 'rmse', 'r2')} for group in ('train', 'test')]
    holdout = [dict(row_index=index, actual=rows[index][3], reference_prediction=baseline,
                    candidate_prediction=prediction, prediction_delta=_number(prediction - baseline))
               for index, baseline, prediction in zip(split['test_indices'], workload['test_predictions'], predictions)]
    public = dict(schema_version=1, profile=PROFILE, result='passed', request_sha256=digest(request),
                  identity=deepcopy(reference['identity']), lake_source=deepcopy(reference['lake_source']),
                  dataset_sha256=workload['hashes']['dataset_sha256'], split_sha256=workload['hashes']['split_sha256'],
                  source_model_sha256=digest(reference['model']),
                  reference=dict(alpha=1.0, metrics=reference_metrics),
                  candidate=dict(alpha=request['alpha'], algorithm='Ridge', solver='svd', fit_intercept=True,
                                 model=model, metrics=metrics),
                  metric_deltas=dict(zip(('train', 'test'), differences)), holdout=holdout,
                  versions=deepcopy(versions), scope=SCOPE, error_code=None)
    require(len(canonical(public)) + 1 <= MAX_PUBLIC_BYTES)
    return public


def compare(request):
    """Fit exactly one candidate; saved alpha=1 is the comparison reference."""
    request = normalize_request(request)
    def calculate(workload, reference):
        train = [deepcopy(workload['dataset']['rows'][i]) for i in workload['split']['train_indices']]
        try:
            model, versions = _fit(train, request['alpha'])
        except Exception:
            raise ComparisonError('COMPARISON_FIT_FAILED') from None
        return _project(request, workload, reference, model, versions)
    return _saved(request, calculate)


def validate_result(value, request):
    """Rebind a worker result and replay its arithmetic without fitting."""
    request = normalize_request(request)
    require(type(value) is dict and set(value) == PUBLIC_KEYS and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['profile'] == PROFILE and value['result'] == 'passed'
            and type(value['candidate']) is dict and type(value['versions']) is dict)
    def validate(workload, reference):
        expected = _project(request, workload, reference, value['candidate'].get('model'), value['versions'])
        require(canonical(value) == canonical(expected))
        return deepcopy(expected)
    return _saved(request, validate)


def failure(code):
    require(code in ERROR_CODES)
    return dict.fromkeys(PUBLIC_KEYS) | dict(schema_version=1, profile=PROFILE, result='failed', scope=SCOPE, error_code=code)


def main(argv=None, *, stdin=None, stdout=None):
    stdin, stdout = (sys.stdin.buffer if stdin is None else stdin), (sys.stdout if stdout is None else stdout)
    first = None
    try:
        args = sys.argv[1:] if argv is None else argv
        require(type(args) is list and args == ['--request-stdin'], 'COMPARISON_ARGUMENTS_INVALID')
        public = compare(parse_request(stdin.read(MAX_INPUT_BYTES + 1)))
    except Exception as error:
        code = str(error) if type(error) is ComparisonError and str(error) in ERROR_CODES else 'COMPARISON_SOURCE_INVALID'
        public = failure(code)
    except BaseException as error:
        first, public = error, failure('COMPARISON_INTERRUPTED')
    try:
        stdout.write(canonical(public).decode('ascii') + '\n')
        stdout.flush()
    except BaseException as error:
        first = T._keep(first, error)
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
