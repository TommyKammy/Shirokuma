#!/usr/bin/env python3
"""Bounded arithmetic predictions from one validated saved Iceberg Ridge model.

No training, executable model deserialization, HTTP, SDK, Reader, environment
configuration or output files. The shared loader owns saved-source validation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).absolute().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import training as T

PROFILE = 'lake-batch-prediction-v1'
FEATURES = ('x1', 'x2', 'x3')
MAX_ROWS = 64
MAX_INPUT_BYTES = 32 * 1024
MAX_PUBLIC_BYTES = 32 * 1024
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
SCOPE = 'Saved Iceberg Ridge arithmetic predictions; no new fit or live source read, not general model quality or online Serving.'
ERROR_CODES = frozenset({'PREDICTION_ARGUMENTS_INVALID', 'PREDICTION_REQUEST_INVALID',
                         'PREDICTION_SOURCE_INVALID', 'PREDICTION_NUMBER_INVALID', 'PREDICTION_INTERRUPTED'})
PUBLIC_KEYS = frozenset({'schema_version', 'profile', 'result', 'request_sha256', 'identity',
                         'lake_source', 'model_sha256', 'predictions', 'error_code', 'scope'})


class PredictionError(ValueError):
    """Only fixed error codes cross the CLI boundary."""


def require(condition, code):
    if not condition:
        raise PredictionError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def normalize_request(value):
    code = 'PREDICTION_REQUEST_INVALID'
    require(type(value) is dict and set(value) == {'schema_version', 'rows'}
            and type(value['schema_version']) is int and value['schema_version'] == 1, code)
    require(type(value['rows']) is list and 1 <= len(value['rows']) <= MAX_ROWS, code)
    for row in value['rows']:
        require(type(row) is dict and set(row) == set(FEATURES), code)
        for number in row.values():
            require(type(number) in (int, float), code)
            try:
                require(math.isfinite(number), code)
            except OverflowError:
                raise PredictionError(code) from None
    require(len(canonical(value)) <= MAX_INPUT_BYTES, code)
    return deepcopy(value)


def parse_request(raw):
    code = 'PREDICTION_REQUEST_INVALID'
    require(type(raw) is bytes and 0 < len(raw) <= MAX_INPUT_BYTES, code)
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, code)
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False, code))
        return normalize_request(value)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise PredictionError(code) from None


def predict(token, request):
    require(type(token) is str and TOKEN.fullmatch(token), 'PREDICTION_ARGUMENTS_INVALID')
    request = normalize_request(request)
    try:
        saved = T.load_saved_review(token, kind='iceberg')
        T.validate_public(saved)
        require(saved['profile'] == T.LAKE_PROFILE and saved['identity']['attempt_token'] == token,
                'PREDICTION_SOURCE_INVALID')
    except Exception:
        raise PredictionError('PREDICTION_SOURCE_INVALID') from None
    model = saved['model']
    predictions = []
    for row in request['rows']:
        try:
            number = math.fsum([model['intercept'], *(
                coefficient * row[feature] for coefficient, feature in zip(model['coefficients'], FEATURES))])
            require(math.isfinite(number), 'PREDICTION_NUMBER_INVALID')
        except (ValueError, OverflowError):
            raise PredictionError('PREDICTION_NUMBER_INVALID') from None
        predictions.append(number)
    public = dict(schema_version=1, profile=PROFILE, result='passed', request_sha256=digest(request),
                  identity=deepcopy(saved['identity']), lake_source=deepcopy(saved['lake_source']),
                  model_sha256=digest(model), predictions=predictions, error_code=None, scope=SCOPE)
    require(len(canonical(public)) + 1 <= MAX_PUBLIC_BYTES, 'PREDICTION_NUMBER_INVALID')
    return public


def failure(code):
    require(code in ERROR_CODES, 'PREDICTION_SOURCE_INVALID')
    return dict(schema_version=1, profile=PROFILE, result='failed', request_sha256=None,
                identity=None, lake_source=None, model_sha256=None, predictions=None, error_code=code, scope=SCOPE)


def main(argv=None, *, stdin=None, stdout=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    original = None
    try:
        args = sys.argv[1:] if argv is None else argv
        require(type(args) is list and len(args) == 3 and args[0] == '--training-token'
                and type(args[1]) is str and TOKEN.fullmatch(args[1])
                and args[2] == '--request-stdin', 'PREDICTION_ARGUMENTS_INVALID')
        raw = stdin.read(MAX_INPUT_BYTES + 1)
        if type(raw) is str:
            raw = raw.encode('utf-8')
        public = predict(args[1], parse_request(raw))
    except Exception as error:
        code = str(error) if type(error) is PredictionError and str(error) in ERROR_CODES else 'PREDICTION_SOURCE_INVALID'
        public = failure(code)
    except BaseException as error:
        original, public = error, failure('PREDICTION_INTERRUPTED')
    try:
        stdout.write(canonical(public).decode('ascii') + '\n')
        stdout.flush()
    except BaseException as error:
        if original is None or (isinstance(original, Exception) and not isinstance(error, Exception)):
            original = error
    if original is not None:
        if not isinstance(original, Exception):
            raise original
        return 1
    return 0 if public['result'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
