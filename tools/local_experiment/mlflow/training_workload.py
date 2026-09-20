"""One reproducible synthetic Ridge workload with an inspectable JSON model.

This module performs no tracking, file access, downloads or model loading. The
128 generated rows describe a small functional experiment, not general model
quality. Validation replays coefficients and checks the training-only Ridge
normal equations; it never fits another model or executes serialized code.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import sys

PROFILE = 'fixed-supervised-regression-v1'
FEATURES = ['x1','x2','x3']
ROWS, TRAIN_ROWS, TEST_ROWS = 128, 96, 32
DATASET_SEED, SPLIT_SEED = 1729, 1730
PARAMS = {'algorithm':'Ridge','alpha':1.0,'solver':'svd','fit_intercept':True,'dataset_seed':DATASET_SEED}
RESULT_KEYS = {'schema_version','profile','dataset','split','params','model','baseline',
               'metrics','test_predictions','hashes','versions'}
METRIC_KEYS = {'mae','rmse','r2'}
MAX_RESULT_BYTES = 128 * 1024
REL_TOL, ABS_TOL = 1e-11, 1e-11


class TrainingError(ValueError):
    """A fixed code without dataset values or underlying library messages."""


def require(condition, code='TRAINING_RESULT_INVALID'):
    if not condition:
        raise TrainingError(code)


def canonical(value):
    try:
        return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                          allow_nan=False).encode('ascii')
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise TrainingError('TRAINING_JSON_INVALID') from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _number(value):
    require(type(value) in (int,float), 'TRAINING_NUMBER_INVALID')
    try:
        number = float(value)
    except (OverflowError,ValueError):
        raise TrainingError('TRAINING_NUMBER_INVALID') from None
    require(math.isfinite(number), 'TRAINING_NUMBER_INVALID')
    return number


def _same_number(actual, expected):
    require(math.isclose(_number(actual),_number(expected),rel_tol=REL_TOL,abs_tol=ABS_TOL),
            'TRAINING_REPLAY_MISMATCH')


def _dataset():
    import numpy as np
    random = np.random.default_rng(DATASET_SEED)
    features = random.normal(size=(ROWS,len(FEATURES)))
    noise = random.normal(loc=0.0,scale=0.2,size=ROWS)
    targets = 4.0 + 2.5*features[:,0] - 1.75*features[:,1] + 0.5*features[:,2] + noise
    return {'feature_names':list(FEATURES),'target_name':'target',
            'rows':np.column_stack((features,targets)).tolist()}


def _split():
    import numpy as np
    indices = np.random.default_rng(SPLIT_SEED).permutation(ROWS).tolist()
    return {'seed':SPLIT_SEED,'train_indices':indices[:TRAIN_ROWS],'test_indices':indices[TRAIN_ROWS:]}


def _predict(rows, coefficients, intercept):
    return [math.fsum([intercept,*(coefficient*value for coefficient,value in zip(coefficients,row[:3]))])
            for row in rows]


def _metrics(targets, predictions):
    require(len(targets) == len(predictions) and len(targets) > 1)
    count = len(targets)
    mean = math.fsum(targets)/count
    squared_error = math.fsum((target-prediction)**2 for target,prediction in zip(targets,predictions))
    total_variation = math.fsum((target-mean)**2 for target in targets)
    require(total_variation > 0, 'TRAINING_TARGET_VARIATION_INVALID')
    return {'mae':math.fsum(abs(target-prediction) for target,prediction in zip(targets,predictions))/count,
            'rmse':math.sqrt(squared_error/count), 'r2':1.0-squared_error/total_variation}


def _ridge_optimum(rows, coefficients, intercept):
    """Independently check the unique alpha=1 optimum using training rows only."""
    feature_means = [math.fsum(row[column] for row in rows)/len(rows) for column in range(3)]
    target_mean = math.fsum(row[3] for row in rows)/len(rows)
    _same_number(intercept, target_mean-math.fsum(coefficient*mean for coefficient,mean in zip(coefficients,feature_means)))
    for column in range(3):
        gradient = math.fsum((row[column]-feature_means[column]) *
            (math.fsum((row[j]-feature_means[j])*coefficients[j] for j in range(3))-(row[3]-target_mean))
            for row in rows) + PARAMS['alpha']*coefficients[column]
        require(math.isfinite(gradient) and abs(gradient) <= 1e-8, 'TRAINING_MODEL_NOT_TRAIN_OPTIMUM')


def validate_result(value):
    """Validate fixed data/split, typed metrics and JSON coefficient replay.

    Recorded package versions are bounded provenance claims. Artifact/source
    hashes and process ownership are checked by the surrounding writer.
    """
    require(type(value) is dict and set(value) == RESULT_KEYS)
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE)
    require(len(canonical(value)) <= MAX_RESULT_BYTES, 'TRAINING_RESULT_LIMIT')
    dataset, split = value['dataset'], value['split']
    require(type(dataset) is dict and set(dataset) == {'feature_names','target_name','rows'})
    require(type(dataset['feature_names']) is list and dataset['feature_names'] == FEATURES
            and dataset['target_name'] == 'target')
    rows = dataset['rows']
    require(type(rows) is list and len(rows) == ROWS)
    for row in rows:
        require(type(row) is list and len(row) == 4)
        for cell in row: _number(cell)
    require(dataset == _dataset(), 'TRAINING_DATASET_CHANGED')
    require(type(split) is dict and set(split) == {'seed','train_indices','test_indices'}
            and type(split['seed']) is int and split['seed'] == SPLIT_SEED)
    for key,count in (('train_indices',TRAIN_ROWS),('test_indices',TEST_ROWS)):
        require(type(split[key]) is list and len(split[key]) == count
                and all(type(index) is int and 0 <= index < ROWS for index in split[key]))
    require(len(set(split['train_indices'])) == TRAIN_ROWS and len(set(split['test_indices'])) == TEST_ROWS
            and set(split['train_indices']).isdisjoint(split['test_indices'])
            and set(split['train_indices']) | set(split['test_indices']) == set(range(ROWS)),
            'TRAINING_SPLIT_INVALID')
    require(split == _split(), 'TRAINING_SPLIT_CHANGED')
    params = value['params']
    require(type(params) is dict and set(params) == set(PARAMS)
            and params == PARAMS and type(params['alpha']) is float
            and params['fit_intercept'] is True and type(params['dataset_seed']) is int)
    hashes = value['hashes']
    require(type(hashes) is dict and set(hashes) == {'dataset_sha256','split_sha256'}
            and hashes == {'dataset_sha256':digest(dataset),'split_sha256':digest(split)}, 'TRAINING_HASH_MISMATCH')
    versions = value['versions']
    require(type(versions) is dict and set(versions) == {'python','numpy','scikit_learn'}
            and all(type(version) is str and len(version) <= 64
                    and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,32}',version)
                    for version in versions.values()), 'TRAINING_VERSION_INVALID')
    model = value['model']
    require(type(model) is dict and set(model) == {'coefficients','intercept'}
            and type(model['coefficients']) is list and len(model['coefficients']) == len(FEATURES))
    coefficients = [_number(item) for item in model['coefficients']]
    intercept = _number(model['intercept'])
    require(all(abs(item) <= 1_000_000 for item in [*coefficients,intercept]), 'TRAINING_MODEL_INVALID')
    train_rows = [rows[index] for index in split['train_indices']]
    test_rows = [rows[index] for index in split['test_indices']]
    _ridge_optimum(train_rows,coefficients,intercept)
    baseline = value['baseline']
    require(type(baseline) is dict and set(baseline) == {'prediction'})
    _same_number(baseline['prediction'],math.fsum(row[3] for row in train_rows)/TRAIN_ROWS)
    train_predictions, test_predictions = _predict(train_rows,coefficients,intercept), _predict(test_rows,coefficients,intercept)
    require(type(value['test_predictions']) is list and len(value['test_predictions']) == TEST_ROWS)
    for actual,expected in zip(value['test_predictions'],test_predictions): _same_number(actual,expected)
    expected_metrics = {'train':_metrics([row[3] for row in train_rows],train_predictions),
                        'test':_metrics([row[3] for row in test_rows],test_predictions),
                        'baseline_test':_metrics([row[3] for row in test_rows],[_number(baseline['prediction'])]*TEST_ROWS)}
    require(type(value['metrics']) is dict and set(value['metrics']) == set(expected_metrics))
    for partition,expected in expected_metrics.items():
        actual = value['metrics'][partition]
        require(type(actual) is dict and set(actual) == METRIC_KEYS)
        for metric,number in expected.items(): _same_number(actual[metric],number)


def train():
    """Fit once on 96 rows, evaluate the held-out 32, return ordinary JSON data."""
    import numpy as np
    import sklearn
    from sklearn.linear_model import Ridge

    dataset, split = _dataset(), _split()
    rows = np.asarray(dataset['rows'],dtype=np.float64)
    train_indices,test_indices = split['train_indices'],split['test_indices']
    model = Ridge(alpha=PARAMS['alpha'],solver=PARAMS['solver'],fit_intercept=True)
    model.fit(rows[train_indices,:3],rows[train_indices,3])
    train_predictions = model.predict(rows[train_indices,:3]).tolist()
    test_predictions = model.predict(rows[test_indices,:3]).tolist()
    train_targets,test_targets = rows[train_indices,3].tolist(),rows[test_indices,3].tolist()
    baseline = math.fsum(train_targets)/TRAIN_ROWS
    value = dict(schema_version=1,profile=PROFILE,dataset=dataset,split=split,params=deepcopy(PARAMS),
        model={'coefficients':model.coef_.tolist(),'intercept':float(model.intercept_)},
        baseline={'prediction':baseline},test_predictions=test_predictions,
        metrics={'train':_metrics(train_targets,train_predictions),'test':_metrics(test_targets,test_predictions),
                 'baseline_test':_metrics(test_targets,[baseline]*TEST_ROWS)},
        hashes={'dataset_sha256':digest(dataset),'split_sha256':digest(split)},
        versions={'python':'.'.join(str(part) for part in sys.version_info[:3]),
                  'numpy':np.__version__,'scikit_learn':sklearn.__version__})
    validate_result(value)
    return value
