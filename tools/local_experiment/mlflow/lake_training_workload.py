"""Fit one bounded Ridge model from a validated Iceberg Reader observation.

The supplied typed rows are the only dataset. This module has no file, network,
tracking or table access. The surrounding owner binds fixture and Reader
receipts; this contract retains their identity and original row-order digest.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
import sys

from tools.local_experiment.mlflow import training_workload as W
from tools.local_experiment.query_read import contract as C

PROFILE = 'fixed-iceberg-regression-v1'
FIXTURE_PROFILE = 'fixed-iceberg-regression-source-v1'
FEATURES = ['x1', 'x2', 'x3']
ROWS, TRAIN_ROWS, TEST_ROWS = 64, 48, 16
SPLIT_SEED = 1730
PARAMS = {'algorithm':'Ridge', 'alpha':1.0, 'solver':'svd', 'fit_intercept':True}
RESULT_KEYS = W.RESULT_KEYS | {'lake_input'}
INPUT_KEYS = {'fixture_profile', 'query_evidence_token', 'observation', 'split_row_ids'}
MAX_RESULT_BYTES = 128 * 1024
TrainingError = W.TrainingError
canonical, digest = W.canonical, W.digest


def require(condition, code='LAKE_TRAINING_INPUT_INVALID'):
    W.require(condition, code)


def _number(value):
    number = W._number(value)
    require(abs(number) <= 1_000_000, 'LAKE_TRAINING_NUMBER_LIMIT')
    return number


def _split():
    import numpy as np
    indices = np.random.default_rng(SPLIT_SEED).permutation(ROWS).tolist()
    return {'seed':SPLIT_SEED, 'train_indices':indices[:TRAIN_ROWS], 'test_indices':indices[TRAIN_ROWS:]}


def _observation(value):
    """Require all 64 canonical Reader rows, with no SQL ordering assumption."""
    try:
        C.receipt_projection(value)
    except C.ContractError:
        raise TrainingError('LAKE_TRAINING_READER_INVALID') from None
    require(re.fullmatch(r'iceberg\.local_experiment\.training_[0-9a-f]{32}', value['table']))
    require(value['requested_limit'] == 100 and value['row_count'] == value['fetched_row_count'] == ROWS
            and value['truncated'] is False)
    columns = value['columns']
    require([column['name'] for column in columns] == ['row_id', *FEATURES, 'target']
            and [column['type'] for column in columns] == ['bigint', 'double', 'double', 'double', 'double'])
    # Iceberg optional columns are allowed, but a training cell can never be NULL.
    numeric = []
    for row in value['rows']:
        require(all(cell['value'] is not None for cell in row))
        row_id = int(row[0]['value'])
        require(0 <= row_id < ROWS)
        numeric.append((row_id, [_number(float(cell['value'])) for cell in row[1:]]))
    require(len({row_id for row_id, _ in numeric}) == ROWS)
    numeric.sort(key=lambda item:item[0])
    return {'feature_names':list(FEATURES), 'target_name':'target', 'rows':[row for _, row in numeric]}


def normalize_input(observation, *, evidence_token, fixture_profile=FIXTURE_PROFILE):
    """Copy a Reader result and attach its external evidence token and row IDs."""
    require(type(evidence_token) is str and re.fullmatch(r'[0-9a-f]{32}', evidence_token))
    require(fixture_profile == FIXTURE_PROFILE and type(fixture_profile) is str)
    _observation(observation)
    split = _split()
    result = {'fixture_profile':fixture_profile, 'query_evidence_token':evidence_token,
              'observation':deepcopy(observation),
              'split_row_ids':{'train':split['train_indices'], 'test':split['test_indices']}}
    require(len(canonical(result)) <= MAX_RESULT_BYTES, 'LAKE_TRAINING_INPUT_LIMIT')
    return result


def _input(value):
    require(type(value) is dict and set(value) == INPUT_KEYS)
    normalized = normalize_input(value['observation'], evidence_token=value['query_evidence_token'],
                                 fixture_profile=value['fixture_profile'])
    row_ids = value['split_row_ids']
    require(type(row_ids) is dict and set(row_ids) == {'train', 'test'})
    for key, count in (('train', TRAIN_ROWS), ('test', TEST_ROWS)):
        require(type(row_ids[key]) is list and len(row_ids[key]) == count
                and all(type(row_id) is int and 0 <= row_id < ROWS for row_id in row_ids[key]))
    require(value == normalized, 'LAKE_TRAINING_SPLIT_CHANGED')
    return normalized, _observation(normalized['observation'])


def validate_result(value):
    """Rebuild numeric rows from original Reader strings and replay the model.

No dataset is regenerated and no model is fitted during validation. The train
partition alone must satisfy the alpha=1 Ridge normal equations.
    """
    require(type(value) is dict and set(value) == RESULT_KEYS, 'LAKE_TRAINING_RESULT_INVALID')
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and value['profile'] == PROFILE)
    require(len(canonical(value)) <= MAX_RESULT_BYTES, 'LAKE_TRAINING_RESULT_LIMIT')
    _, expected_dataset = _input(value['lake_input'])
    dataset, split = value['dataset'], value['split']
    require(type(dataset) is dict and set(dataset) == {'feature_names', 'target_name', 'rows'})
    require(type(dataset['rows']) is list and len(dataset['rows']) == ROWS)
    for row in dataset['rows']:
        require(type(row) is list and len(row) == 4)
        for cell in row: _number(cell)
    require(canonical(dataset) == canonical(expected_dataset), 'LAKE_TRAINING_DATASET_CHANGED')
    require(type(split) is dict and set(split) == {'seed', 'train_indices', 'test_indices'}
            and type(split['seed']) is int and split['seed'] == SPLIT_SEED)
    for key, count in (('train_indices', TRAIN_ROWS), ('test_indices', TEST_ROWS)):
        require(type(split[key]) is list and len(split[key]) == count
                and all(type(index) is int and 0 <= index < ROWS for index in split[key]))
    require(split == _split(), 'LAKE_TRAINING_SPLIT_CHANGED')
    params = value['params']
    require(type(params) is dict and set(params) == set(PARAMS) and params == PARAMS
            and type(params['alpha']) is float and params['fit_intercept'] is True)
    require(type(value['hashes']) is dict and value['hashes'] ==
            {'dataset_sha256':digest(dataset), 'split_sha256':digest(split)}, 'LAKE_TRAINING_HASH_MISMATCH')
    versions = value['versions']
    require(type(versions) is dict and set(versions) == {'python', 'numpy', 'scikit_learn'}
            and all(type(version) is str and len(version) <= 64
                    and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,32}', version)
                    for version in versions.values()))
    model = value['model']
    require(type(model) is dict and set(model) == {'coefficients', 'intercept'}
            and type(model['coefficients']) is list and len(model['coefficients']) == len(FEATURES))
    coefficients = [_number(item) for item in model['coefficients']]
    intercept = _number(model['intercept'])
    train_rows = [dataset['rows'][index] for index in split['train_indices']]
    test_rows = [dataset['rows'][index] for index in split['test_indices']]
    W._ridge_optimum(train_rows, coefficients, intercept)
    baseline = value['baseline']
    require(type(baseline) is dict and set(baseline) == {'prediction'})
    W._same_number(_number(baseline['prediction']), math.fsum(row[3] for row in train_rows)/TRAIN_ROWS)
    train_predictions = W._predict(train_rows, coefficients, intercept)
    test_predictions = W._predict(test_rows, coefficients, intercept)
    require(type(value['test_predictions']) is list and len(value['test_predictions']) == TEST_ROWS)
    for actual, expected in zip(value['test_predictions'], test_predictions):
        W._same_number(_number(actual), expected)
    expected_metrics = {'train':W._metrics([row[3] for row in train_rows], train_predictions),
                       'test':W._metrics([row[3] for row in test_rows], test_predictions),
                       'baseline_test':W._metrics([row[3] for row in test_rows], [baseline['prediction']]*TEST_ROWS)}
    require(type(value['metrics']) is dict and set(value['metrics']) == set(expected_metrics))
    for group, expected in expected_metrics.items():
        actual = value['metrics'][group]
        require(type(actual) is dict and set(actual) == W.METRIC_KEYS)
        for metric, number in expected.items(): W._same_number(_number(actual[metric]), number)


def train(lake_input):
    """Fit once on 48 supplied rows and evaluate the untouched 16-row holdout."""
    source, dataset = _input(lake_input)
    import numpy as np
    import sklearn
    from sklearn.linear_model import Ridge

    split = _split()
    rows = np.asarray(dataset['rows'], dtype=np.float64)
    train_indices, test_indices = split['train_indices'], split['test_indices']
    model = Ridge(alpha=1.0, solver='svd', fit_intercept=True)
    model.fit(rows[train_indices, :3], rows[train_indices, 3])
    train_predictions = model.predict(rows[train_indices, :3]).tolist()
    test_predictions = model.predict(rows[test_indices, :3]).tolist()
    train_targets, test_targets = rows[train_indices, 3].tolist(), rows[test_indices, 3].tolist()
    baseline = math.fsum(train_targets)/TRAIN_ROWS
    value = dict(schema_version=1, profile=PROFILE, dataset=dataset, split=split, params=deepcopy(PARAMS),
        lake_input=source, model={'coefficients':model.coef_.tolist(), 'intercept':float(model.intercept_)},
        baseline={'prediction':baseline}, test_predictions=test_predictions,
        metrics={'train':W._metrics(train_targets, train_predictions), 'test':W._metrics(test_targets, test_predictions),
                 'baseline_test':W._metrics(test_targets, [baseline]*TEST_ROWS)},
        hashes={'dataset_sha256':digest(dataset), 'split_sha256':digest(split)},
        versions={'python':'.'.join(str(part) for part in sys.version_info[:3]),
                  'numpy':np.__version__, 'scikit_learn':sklearn.__version__})
    validate_result(value)
    return value
