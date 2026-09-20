"""Pure contract for one fixed, training-only sklearn autolog run."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from tools.local_experiment.mlflow import registry_contract as K
from tools.local_experiment.notebook import training as T

PROFILE = 'fixed-ridge-autolog-v1'
EXPERIMENT = 'shirokuma-local-ridge-autolog-v1'
NAMESPACE = 'ridge-autolog'
INDEX_NAME = 'autolog-index.json'
FOLDER = 'autolog'
ARTIFACT = 'estimator.html'
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_RUNS = 128
SCOPE = 'Fixed saved 48-row training partition; worker sklearn autolog; no independent holdout assessment, executable model, Registry write or kernel fit.'
PARAMS = {'alpha': '1.0', 'copy_X': 'True', 'fit_intercept': 'True', 'max_iter': 'None',
          'positive': 'False', 'random_state': 'None', 'solver': 'svd', 'tol': '0.0001'}
METRICS = {'training_score', 'training_mean_squared_error', 'training_mean_absolute_error',
           'training_r2_score', 'training_root_mean_squared_error'}
AUTOLOG_OPTIONS = dict(log_models=False, log_datasets=False, log_input_examples=False,
                      log_model_signatures=False, log_post_training_metrics=False,
                      exclusive=False, silent=False)
SOURCE_KEYS = {'schema_version', 'profile', 'binding', 'train_rows_sha256', 'train_rows', 'holdout_rows', 'params'}
ENTRY_KEYS = {'evidence_token', 'run_id', 'source_sha256', 'started_ms', 'finished_ms', 'completed', 'phase'}
RESULT_KEYS = {'schema_version', 'profile', 'evidence_token', 'training_token', 'store_id',
               'experiment_id', 'run_id', 'result', 'phase', 'started_ms', 'finished_ms',
               'source_sha256', 'source_unchanged', 'failure_code', 'model', 'versions',
               'run', 'artifact', 'backup_sha256'}
NATIVE_TAGS = {'mlflow.user', 'mlflow.source.name', 'mlflow.source.type', 'mlflow.runName', 'mlflow.autologging'}
SHA = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')


class AutologError(RuntimeError):
    pass


def require(value, code='AUTOLOG_INVALID'):
    if not value:
        raise AutologError(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def token(value):
    require(type(value) is str and TOKEN.fullmatch(value), 'AUTOLOG_TOKEN_INVALID')
    return value


def numeric_id(value):
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,19}', value))
    return value


def _time(value):
    require(type(value) is int and 0 < value <= 2**53)
    return value


def _number(value):
    require(type(value) in (int, float) and math.isfinite(value))
    return value


def _hash(value):
    require(type(value) is str and SHA.fullmatch(value))


def source(receipt, workload, *, store_id):
    # These are the same completed-source checks as the public saved-review loader.
    T._workload(workload, kind='iceberg')
    binding = K.binding(receipt, workload, store_id=store_id)
    train = [workload['dataset']['rows'][i] for i in workload['split']['train_indices']]
    require(len(train) == 48 and len(workload['split']['test_indices']) == 16)
    return dict(schema_version=1, profile=PROFILE, binding=binding,
                train_rows_sha256=digest(train), train_rows=48, holdout_rows=16, params=deepcopy(PARAMS))


def validate_source(value, workload, receipt, *, store_id):
    validate_source_shape(value, store_id=store_id)
    require(value == source(receipt, workload, store_id=store_id), 'AUTOLOG_SOURCE_INVALID')
    return deepcopy(value)


def validate_source_shape(value, *, store_id):
    require(type(value) is dict and set(value) == SOURCE_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE and value['params'] == PARAMS
            and type(value['train_rows']) is int and value['train_rows'] == 48
            and type(value['holdout_rows']) is int and value['holdout_rows'] == 16)
    K.validate_binding(value['binding']); require(value['binding']['store_id'] == store_id)
    _hash(value['train_rows_sha256'])


def experiment_tags(store_id):
    K._store(store_id)
    return {'shirokuma.owner': EXPERIMENT, 'shirokuma.profile': PROFILE, 'shirokuma.store_id': store_id}


def run_tags(source):
    binding = source['binding']
    return dict(experiment_tags(binding['store_id']), **{
        'shirokuma.training_token': binding['training_token'],
        'shirokuma.source_run_id': binding['run_id'],
        'shirokuma.workload_sha256': binding['workload_sha256'],
        'shirokuma.model_sha256': binding['model_sha256']})


def expected_tags(source):
    return dict(run_tags(source), estimator_name='Ridge', estimator_class='sklearn.linear_model._ridge.Ridge')


def empty_index(store_id):
    return dict(schema_version=1, profile=PROFILE, store_id=store_id, experiment_id=None,
                experiment_pending=False, runs={})


def validate_index(value, *, store_id, completed=True):
    K._store(store_id)
    require(type(value) is dict and set(value) == set(empty_index(store_id))
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE and value['store_id'] == store_id
            and type(value['experiment_pending']) is bool and type(value['runs']) is dict
            and len(value['runs']) <= MAX_RUNS)
    if value['experiment_id'] is not None: numeric_id(value['experiment_id'])
    seen, run_ids = set(), set()
    for key, entry in value['runs'].items():
        token(key)
        require(type(entry) is dict and set(entry) == ENTRY_KEYS)
        token(entry['evidence_token']); require(entry['evidence_token'] not in seen); seen.add(entry['evidence_token'])
        _hash(entry['source_sha256']); _time(entry['started_ms'])
        require(type(entry['completed']) is bool and entry['phase'] in ('pending', 'run-created', 'fit-started', 'verified'))
        if entry['run_id'] is not None:
            token(entry['run_id']); require(entry['run_id'] not in run_ids); run_ids.add(entry['run_id'])
        if entry['finished_ms'] is not None: require(_time(entry['finished_ms']) >= entry['started_ms'])
        require(not entry['completed'] or entry['phase'] == 'verified' and entry['run_id'] is not None
                and entry['finished_ms'] is not None)
    require(not value['runs'] or value['experiment_id'] is not None)
    if completed:
        require(not value['experiment_pending'] and all(x['completed'] for x in value['runs'].values()),
                'AUTOLOG_PRIOR_OUTCOME_UNKNOWN')
    return deepcopy(value)


def validate_backup(value, *, store_id):
    require(type(value) is dict and set(value) == {'manifest', 'sha256', 'store_id'}
            and value['store_id'] == store_id)
    path = value['manifest']
    require(type(path) is str and path.startswith('tmp/local-experiment/')
            and path.endswith('/backup-manifest.json') and all(p not in ('', '.', '..') for p in path.split('/')))
    _hash(value['sha256'])
    return deepcopy(value)


def validate_run(value, source, *, experiment_id, run_id):
    require(type(value) is dict and set(value) == {'info', 'params', 'tags', 'metrics', 'histories'})
    info = value['info']
    require(type(info) is dict and set(info) == {'run_id', 'experiment_id', 'status', 'start_time',
            'end_time', 'lifecycle_stage', 'artifact_uri', 'run_name', 'user_id'}
            and info['run_id'] == run_id and info['experiment_id'] == experiment_id
            and info['status'] == 'FINISHED' and info['lifecycle_stage'] == 'active'
            and info['run_name'] == 'autolog-' + source['binding']['training_token']
            and type(info['user_id']) is str and len(info['user_id']) <= 256
            and type(info['artifact_uri']) is str and info['artifact_uri'].startswith('file:///'))
    require(_time(info['end_time']) >= _time(info['start_time']))
    require(value['params'] == PARAMS and type(value['metrics']) is dict and set(value['metrics']) == METRICS
            and type(value['histories']) is dict and set(value['histories']) == METRICS)
    tags = value['tags']; expected = expected_tags(source)
    require(type(tags) is dict and set(expected) <= set(tags) <= set(expected) | NATIVE_TAGS
            and all(tags[k] == v for k, v in expected.items())
            and all(type(v) is str and len(v.encode()) <= 2048 for v in tags.values()))
    require(tags.get('mlflow.runName', info['run_name']) == info['run_name'])
    for key in METRICS:
        metric = _number(value['metrics'][key]); history = value['histories'][key]
        require(type(history) is list and len(history) == 1 and type(history[0]) is list and len(history[0]) == 4)
        name, timestamp, step, number = history[0]
        require(name == key and info['start_time'] <= _time(timestamp) <= info['end_time']
                and type(step) is int and step == 0 and _number(number) == metric)
    return deepcopy(value)


def expected_metrics(model, workload):
    require(type(model) is dict and set(model) == {'coefficients', 'intercept'}
            and type(model['coefficients']) is list and len(model['coefficients']) == 3)
    weights = [_number(x) for x in model['coefficients']]; bias = _number(model['intercept'])
    rows = [workload['dataset']['rows'][i] for i in workload['split']['train_indices']]
    predictions = [math.fsum([bias] + [a*b for a,b in zip(row[:3], weights)]) for row in rows]
    targets = [row[3] for row in rows]; metrics = T._metrics(targets, predictions)
    mse = math.fsum((a-b)**2 for a,b in zip(targets, predictions)) / len(rows)
    return dict(training_score=metrics['r2'], training_mean_squared_error=mse,
                training_mean_absolute_error=metrics['mae'], training_r2_score=metrics['r2'],
                training_root_mean_squared_error=metrics['rmse'])


def validate_calculation(result, workload):
    metrics = expected_metrics(result['model'], workload)
    for key, value in metrics.items():
        require(math.isclose(result['run']['metrics'][key], value, rel_tol=1e-10, abs_tol=1e-10), 'AUTOLOG_METRICS_INVALID')
    actual = result['model']; reference = workload['model']
    require(all(math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-10) for a,b in
                zip(actual['coefficients']+[actual['intercept']], reference['coefficients']+[reference['intercept']])),
            'AUTOLOG_MODEL_MISMATCH')
    return deepcopy(result)


def completed_result(source, original, recovery=None):
    """Select an append-only adoption; never conceal the original failed receipt."""
    if recovery is None:
        return deepcopy(original)
    require(type(recovery) is dict and set(recovery) == {'schema_version', 'profile', 'evidence_token',
            'source_sha256', 'original_result_sha256', 'original_entry', 'original_entry_sha256',
            'recovered_at_ms', 'observation', 'result'}, 'AUTOLOG_RECOVERY_INVALID')
    require(type(original) is dict and set(original) == RESULT_KEYS
            and original['result'] == 'failed' and original['phase'] == 'verified'
            and original['failure_code'] == 'AUTOLOG_ARTIFACT_INVALID'
            and original['source_unchanged'] is True, 'AUTOLOG_RECOVERY_INVALID')
    candidate = deepcopy(original); candidate.update(result='verified', failure_code=None)
    require(type(recovery['schema_version']) is int and recovery['schema_version'] == 1
            and recovery['profile'] == PROFILE and recovery['evidence_token'] == original['evidence_token']
            and recovery['source_sha256'] == digest(source) == original['source_sha256']
            and recovery['original_result_sha256'] == digest(original)
            and recovery['original_entry_sha256'] == digest(recovery['original_entry'])
            and recovery['observation'] == 'stopped-sqlite-read-only' and recovery['result'] == candidate
            and _time(recovery['recovered_at_ms']) >= _time(original['finished_ms']), 'AUTOLOG_RECOVERY_INVALID')
    return candidate


def validate_recovery_entry(recovery, completed_entry):
    require(type(completed_entry) is dict and set(completed_entry) == ENTRY_KEYS,
            'AUTOLOG_RECOVERY_INVALID')
    original = deepcopy(completed_entry)
    original.update(completed=False, phase='fit-started', finished_ms=None)
    require(recovery['original_entry'] == original and completed_entry['completed'] is True
            and completed_entry['phase'] == 'verified'
            and completed_entry['finished_ms'] == recovery['result']['finished_ms'], 'AUTOLOG_RECOVERY_INVALID')
    return deepcopy(completed_entry)


def validate_completed(index, source, result, artifact_bytes, *, store_id, backup):
    validate_index(index, store_id=store_id); validate_source_shape(source, store_id=store_id)
    validate_backup(backup, store_id=store_id)
    require(type(result) is dict and set(result) == RESULT_KEYS and type(result['schema_version']) is int
            and result['schema_version'] == 1 and result['profile'] == PROFILE
            and result['result'] == result['phase'] == 'verified' and result['source_unchanged'] is True
            and result['failure_code'] is None and result['store_id'] == store_id)
    token(result['training_token']); token(result['evidence_token']); token(result['run_id'])
    entry = index['runs'].get(result['training_token'])
    require(entry is not None and entry == dict(evidence_token=result['evidence_token'], run_id=result['run_id'],
            source_sha256=result['source_sha256'], started_ms=result['started_ms'], finished_ms=result['finished_ms'],
            completed=True, phase='verified') and result['source_sha256'] == digest(source)
            and result['backup_sha256'] == digest(backup) and result['experiment_id'] == index['experiment_id']
            and result['training_token'] == source['binding']['training_token'])
    versions = result['versions']
    require(type(versions) is dict and set(versions) == {'python', 'numpy', 'scikit_learn', 'mlflow'}
            and type(versions['python']) is str and re.fullmatch(r'3\.12\.[0-9]+', versions['python'])
            and versions['numpy'] == '2.5.3' and versions['scikit_learn'] == '1.9.1' and versions['mlflow'] == '3.16.1')
    validate_run(result['run'], source, experiment_id=result['experiment_id'], run_id=result['run_id'])
    require(result['started_ms'] <= result['run']['info']['start_time'] <= result['run']['info']['end_time'] <= result['finished_ms'])
    require(type(artifact_bytes) is bytes and 0 < len(artifact_bytes) <= MAX_ARTIFACT_BYTES)
    try: html = artifact_bytes.decode('utf-8')
    except UnicodeError: raise AutologError('AUTOLOG_ARTIFACT_INVALID') from None
    # Pinned MLflow wraps sklearn's representation in <html lang="en">.
    # Retain the simple wrapper accepted by saved fake fixtures as well.
    require(re.search(r'<html(?:\s+lang=(?:"en"|\'en\'))?\s*>', html)
            and '</html>' in html and 'Ridge' in html, 'AUTOLOG_ARTIFACT_INVALID')
    require(result['artifact'] == dict(name=ARTIFACT, size=len(artifact_bytes), sha256=hashlib.sha256(artifact_bytes).hexdigest()))
    # Reject malformed model even when the caller has not yet supplied the original workload.
    model = result['model']
    require(type(model) is dict and set(model) == {'coefficients', 'intercept'}
            and type(model['coefficients']) is list and len(model['coefficients']) == 3)
    for value in model['coefficients'] + [model['intercept']]: _number(value)
    return deepcopy(result)
