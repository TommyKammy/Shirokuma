#!/usr/bin/env python3
"""Fixed train-only Ridge cross-validation, using verified saved JSON only.

Nine fold fits select an alpha; one full-train refit is then evaluated on the
already-seen holdout. No Reader, Tracking write, Registry or executable model.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import compare as C

PROFILE = 'saved-ridge-cv-v1'
ALPHAS = (0.1, 1.0, 10.0)
SEED = 20260920
MAX_INPUT_BYTES, MAX_PUBLIC_BYTES = 4096, 64 * 1024
SCOPE = ('保存48学習行の固定3foldで候補を選び、48行で再学習します。'
         '既視holdout16行は選択後の参考評価だけに使い、独立した最終評価とはしません。')
PUBLIC_KEYS = C.PUBLIC_KEYS | {'cv'}
CV_KEYS = {'seed', 'alphas', 'folds', 'candidates', 'selected_alpha',
           'selection_metric', 'tie_break', 'fit_count'}
ERROR_CODES = {'TUNING_ARGUMENTS_INVALID', 'TUNING_REQUEST_INVALID', 'TUNING_SOURCE_INVALID',
               'TUNING_RESULT_INVALID', 'TUNING_FIT_FAILED', 'TUNING_INTERRUPTED'}
canonical, digest = C.canonical, C.digest


class TuningError(C.ComparisonError):
    """Fixed public reason codes; also preserved by the shared source guard."""


def require(condition, code='TUNING_RESULT_INVALID'):
    if not condition:
        raise TuningError(code)


def normalize_request(value):
    code = 'TUNING_REQUEST_INVALID'
    require(type(value) is dict and set(value) == {'schema_version', 'training_token'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['training_token']) is str
            and re.fullmatch(r'[0-9a-f]{32}', value['training_token']) is not None, code)
    return dict(schema_version=1, training_token=value['training_token'])


def parse_request(raw):
    code = 'TUNING_REQUEST_INVALID'
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
        raise TuningError(code) from None


def _saved(request, operation):
    try:
        return C._saved(request, operation)
    except TuningError:
        raise
    except C.ComparisonError as error:
        code = 'TUNING_SOURCE_INVALID' if str(error) == 'COMPARISON_SOURCE_INVALID' else 'TUNING_RESULT_INVALID'
        raise TuningError(code) from None


def folds(train_indices):
    """Validation chunks follow the shuffle; fitting keeps original train order."""
    require(type(train_indices) is list and len(train_indices) == 48
            and all(type(i) is int and 0 <= i < 64 for i in train_indices)
            and len(set(train_indices)) == 48)
    shuffled = train_indices.copy()
    random.Random(SEED).shuffle(shuffled)
    result = []
    for start in range(0, 48, 16):
        validation = shuffled[start:start + 16]
        result.append(dict(train_indices=[i for i in train_indices if i not in validation],
                           validation_indices=validation))
    return result


def _fold_model(value, train, alpha):
    """Check the 32-row centered normal equations, without fitting again."""
    require(type(value) is dict and set(value) == {'coefficients', 'intercept'}
            and type(value['coefficients']) is list and len(value['coefficients']) == 3
            and len(train) == 32)
    coefficients = [C._number(x) for x in value['coefficients']]
    intercept = C._number(value['intercept'])
    means = [math.fsum(row[j] for row in train) / len(train) for j in range(4)]
    require(math.isclose(intercept, means[3] - math.fsum(coefficients[j] * means[j] for j in range(3)),
                         rel_tol=1e-10, abs_tol=1e-8))
    for j in range(3):
        gradient = math.fsum((row[j] - means[j]) * (
            math.fsum((row[k] - means[k]) * coefficients[k] for k in range(3)) -
            (row[3] - means[3])) for row in train) + alpha * coefficients[j]
        require(math.isfinite(gradient) and abs(gradient) <= 1e-7)
    return dict(coefficients=coefficients, intercept=intercept)


def _candidate(alpha, fold_models, partition, rows):
    require(type(fold_models) is list and len(fold_models) == 3)
    evaluations = []
    for fold, raw_model in zip(partition, fold_models):
        train = [rows[i] for i in fold['train_indices']]
        validation = [rows[i] for i in fold['validation_indices']]
        model = _fold_model(raw_model, train, alpha)
        targets = [row[3] for row in validation]
        predictions = C._predict(validation, model)
        metrics = C.T._metrics(targets, predictions)
        metrics['mse'] = math.fsum((a - p) ** 2 for a, p in zip(targets, predictions)) / 16
        for number in metrics.values():
            C._number(number)
        evaluations.append(dict(model=model, validation_metrics=metrics))
    mean_mse = C._number(math.fsum(item['validation_metrics']['mse'] for item in evaluations) / 3)
    return dict(alpha=alpha, folds=evaluations, mean_validation_mse=mean_mse)


def select_alpha(candidates):
    """An exact mean-MSE tie selects the smaller alpha, not the holdout score."""
    require(type(candidates) is list and len(candidates) == 3)
    require([item.get('alpha') for item in candidates] == list(ALPHAS))
    for item in candidates:
        require(C._number(item['mean_validation_mse']) >= 0)
    return min(candidates, key=lambda item: (item['mean_validation_mse'], item['alpha']))['alpha']


def _project(request, workload, reference, candidates, model, versions):
    partition = folds(workload['split']['train_indices'])
    rows = workload['dataset']['rows']
    require(type(candidates) is list and len(candidates) == 3)
    verified = []
    for alpha, candidate in zip(ALPHAS, candidates):
        require(type(candidate) is dict and set(candidate) == {'alpha', 'folds', 'mean_validation_mse'}
                and candidate['alpha'] == alpha and type(candidate['folds']) is list
                and len(candidate['folds']) == 3)
        for item in candidate['folds']:
            require(type(item) is dict and set(item) == {'model', 'validation_metrics'})
        expected = _candidate(alpha, [item['model'] for item in candidate['folds']], partition, rows)
        require(canonical(candidate) == canonical(expected))
        verified.append(expected)
    alpha = select_alpha(verified)
    public = C._project(dict(request, alpha=alpha), workload, reference, model, versions)
    public.update(profile=PROFILE, request_sha256=digest(request), scope=SCOPE,
                  cv=dict(seed=SEED, alphas=list(ALPHAS), folds=partition, candidates=verified,
                          selected_alpha=alpha, selection_metric='mean-validation-mse',
                          tie_break='alpha-ascending', fit_count=10))
    require(len(canonical(public)) + 1 <= MAX_PUBLIC_BYTES)
    return public


def tune(request):
    request = normalize_request(request)
    def calculate(workload, reference):
        partition = folds(workload['split']['train_indices'])
        rows = workload['dataset']['rows']
        candidates, versions = [], None
        def fit(indices, alpha):
            nonlocal versions
            try:
                model, observed_versions = C._fit([deepcopy(rows[i]) for i in indices], alpha)
            except Exception:
                raise TuningError('TUNING_FIT_FAILED') from None
            if versions is not None:
                require(observed_versions == versions)
            versions = deepcopy(observed_versions)
            return model
        for alpha in ALPHAS:
            models = [fit(fold['train_indices'], alpha) for fold in partition]
            candidates.append(_candidate(alpha, models, partition, rows))
        alpha = select_alpha(candidates)
        model = fit(workload['split']['train_indices'], alpha)
        return _project(request, workload, reference, candidates, model, versions)
    return _saved(request, calculate)


def validate_result(value, request):
    """Rebind every fold, metric and coefficient to the saved source; fit zero."""
    request = normalize_request(request)
    require(type(value) is dict and set(value) == PUBLIC_KEYS and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['profile'] == PROFILE and value['result'] == 'passed'
            and type(value['candidate']) is dict and type(value['cv']) is dict and set(value['cv']) == CV_KEYS)
    def validate(workload, reference):
        expected = _project(request, workload, reference, value['cv']['candidates'],
                            value['candidate'].get('model'), value['versions'])
        require(canonical(value) == canonical(expected))
        return deepcopy(expected)
    return _saved(request, validate)


def failure(code):
    require(code in ERROR_CODES)
    return dict.fromkeys(PUBLIC_KEYS) | dict(schema_version=1, profile=PROFILE,
                                            result='failed', scope=SCOPE, error_code=code)


def main(argv=None, *, stdout=None):
    stdout = sys.stdout if stdout is None else stdout
    first = None
    try:
        args = sys.argv[1:] if argv is None else argv
        require(type(args) is list and len(args) == 2 and args[0] == '--training-token',
                'TUNING_ARGUMENTS_INVALID')
        public = tune(normalize_request(dict(schema_version=1, training_token=args[1])))
    except Exception as error:
        code = str(error) if type(error) is TuningError and str(error) in ERROR_CODES else 'TUNING_SOURCE_INVALID'
        public = failure(code)
    except BaseException as error:
        first, public = error, failure('TUNING_INTERRUPTED')
    try:
        stdout.write(canonical(public).decode('ascii') + '\n')
        stdout.flush()
    except BaseException as error:
        first = C.T._keep(first, error)
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
