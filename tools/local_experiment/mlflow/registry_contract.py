"""Pure contract for the single local, safe-JSON Ridge Registry profile."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import uuid

PROFILE = 'local-ridge-registry-v1'
MODEL_NAME = 'shirokuma-local-ridge'
OWNER = 'shirokuma-local-ridge-registry-v1'
INDEX_NAME = 'registry-index.json'
FOLDER = 'registry'
MAX_VERSIONS = 128
TOKEN = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')
BINDING_KEYS = {'training_token', 'run_id', 'experiment_id', 'store_id',
                'workload_sha256', 'model_sha256', 'artifact_relpath'}
MODEL_KEYS = {'name', 'creation_timestamp', 'last_updated_timestamp', 'description', 'tags', 'aliases'}
VERSION_KEYS = {'name', 'version', 'creation_timestamp', 'last_updated_timestamp', 'description',
                'user_id', 'current_stage', 'source', 'run_id', 'status', 'status_message',
                'run_link', 'tags', 'aliases'}
INDEX_KEYS = {'schema_version', 'profile', 'store_id', 'model_name', 'model_pending', 'model', 'entries'}
RESULT_KEYS = {'schema_version', 'profile', 'evidence_token', 'result', 'binding', 'model',
               'model_version', 'created_model', 'created_version', 'source_unchanged',
               'error_code', 'started_ms', 'finished_ms'}
_UNSET = object()


class RegistryError(ValueError):
    pass


def require(condition, code='REGISTRY_INVALID'):
    if not condition:
        raise RegistryError(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def model_digest(value):
    """Existing predict.py hashes compact model JSON without a final newline."""
    return hashlib.sha256(canonical(value)[:-1]).hexdigest()


def token(value):
    require(type(value) is str and TOKEN.fullmatch(value), 'REGISTRY_TOKEN_INVALID')
    return value


def version(value):
    require(type(value) in (int, str), 'REGISTRY_VERSION_INVALID')
    text = str(value)
    require(re.fullmatch(r'[1-9][0-9]{0,18}', text) and int(text) <= 2**63 - 1,
            'REGISTRY_VERSION_INVALID')
    return text


def _store(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise RegistryError('REGISTRY_INVALID') from None


def validate_binding(value):
    require(type(value) is dict and set(value) == BINDING_KEYS)
    token(value['training_token']); token(value['run_id']); _store(value['store_id'])
    require(value['experiment_id'] == '4'
            and all(type(value[k]) is str and SHA.fullmatch(value[k])
                    for k in ('workload_sha256', 'model_sha256'))
            and value['artifact_relpath'] == 'iceberg-training/' + value['run_id'] + '/artifacts/safe-projection.json')
    return deepcopy(value)


def binding(receipt, workload, *, store_id):
    """Caller validates the existing training contract before this projection."""
    require(receipt['result'] == receipt['phase'] == 'verified' and receipt['store_id'] == store_id
            and workload['profile'] == 'fixed-iceberg-regression-v1'
            and digest(workload) == receipt['workload_sha256'])
    model = dict(algorithm='Ridge', alpha=1.0, solver='svd', feature_names=['x1', 'x2', 'x3'],
                 train_rows=48, test_rows=16, **workload['model'])
    return validate_binding(dict(training_token=receipt['token'], run_id=receipt['run_id'],
        experiment_id=receipt['experiment_id'], store_id=store_id,
        workload_sha256=receipt['workload_sha256'], model_sha256=model_digest(model),
        artifact_relpath='iceberg-training/' + receipt['run_id'] + '/artifacts/safe-projection.json'))


def model_tags(store_id):
    _store(store_id)
    return {'shirokuma.owner': OWNER, 'shirokuma.profile': PROFILE, 'shirokuma.store_id': store_id}


def version_tags(value):
    value = validate_binding(value)
    return dict(model_tags(value['store_id']), **{
        'shirokuma.training_token': value['training_token'],
        'shirokuma.workload_sha256': value['workload_sha256']})


def _timestamps(value):
    require(type(value['creation_timestamp']) is int and type(value['last_updated_timestamp']) is int
            and 0 < value['creation_timestamp'] <= value['last_updated_timestamp'] <= 2**53)


def validate_model(value, *, store_id):
    require(type(value) is dict and set(value) == MODEL_KEYS and value['name'] == MODEL_NAME
            and value['description'] is None and value['tags'] == model_tags(store_id)
            and type(value['aliases']) is dict and value['aliases'] == {})
    _timestamps(value)
    return deepcopy(value)


def validate_version(value, expected, *, source_uri, storage_location=_UNSET):
    expected = validate_binding(expected)
    require(type(value) is dict and set(value) == VERSION_KEYS and value['name'] == MODEL_NAME
            and type(value['version']) is str and version(value['version']) == value['version']
            and value['current_stage'] == 'None' and value['status'] == 'READY'
            and value['run_id'] == expected['run_id'] and value['source'] == source_uri
            and type(source_uri) is str and source_uri.startswith('file:///')
            and value['tags'] == version_tags(expected) and value['aliases'] == []
            and all(value[key] is None for key in ('description', 'user_id', 'status_message', 'run_link')))
    require(storage_location is _UNSET or storage_location is None or storage_location == source_uri)
    _timestamps(value)
    return deepcopy(value)


def validate_index(value, *, store_id, allow_pending=False):
    require(type(value) is dict and set(value) == INDEX_KEYS and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['profile'] == PROFILE
            and value['model_name'] == MODEL_NAME and value['store_id'] == store_id
            and type(value['entries']) is dict and len(value['entries']) <= MAX_VERSIONS,
            'REGISTRY_INDEX_INVALID')
    _store(store_id)
    if value['model_pending'] is not None: token(value['model_pending'])
    if value['model'] is not None: validate_model(value['model'], store_id=store_id)
    ids, versions = [], []
    for key, entry in value['entries'].items():
        token(key)
        require(type(entry) is dict and set(entry) == {'state', 'binding', 'evidence_token', 'version'}
                and entry['state'] in ('pending', 'complete'), 'REGISTRY_INDEX_INVALID')
        bound = validate_binding(entry['binding']); token(entry['evidence_token'])
        require(bound['training_token'] == key and bound['store_id'] == store_id, 'REGISTRY_INDEX_INVALID')
        ids.append(entry['evidence_token'])
        if entry['version'] is not None:
            validate_version(entry['version'], bound, source_uri=entry['version']['source'])
            versions.append(entry['version']['version'])
        require(entry['state'] != 'complete' or entry['version'] is not None, 'REGISTRY_INDEX_INVALID')
    require(len(set(ids)) == len(ids) and len(set(versions)) == len(versions)
            and (not value['entries'] or value['model'] is not None), 'REGISTRY_INDEX_INVALID')
    if not allow_pending:
        require(value['model_pending'] is None and all(e['state'] == 'complete' for e in value['entries'].values()),
                'REGISTRY_PRIOR_OUTCOME_UNKNOWN')
    return deepcopy(value)


def validate_completed(index, evidence_token, source, result, expected):
    """Validate original immutable receipts; callers separately bind URI to their artifact root."""
    bound = validate_binding(expected)
    validate_index(index, store_id=bound['store_id'])
    token(evidence_token)
    entry = index['entries'].get(bound['training_token'])
    require(entry is not None and entry['state'] == 'complete' and entry['binding'] == bound
            and entry['evidence_token'] == evidence_token
            and type(source) is dict and set(source) == {'schema_version', 'profile', 'binding'}
            and type(source['schema_version']) is int and source['schema_version'] == 1
            and source['profile'] == PROFILE and source['binding'] == bound, 'REGISTRY_RECEIPT_INVALID')
    require(type(result) is dict and set(result) == RESULT_KEYS
            and type(result['schema_version']) is int and result['schema_version'] == 1
            and result['profile'] == PROFILE and result['evidence_token'] == evidence_token
            and result['result'] == 'verified' and result['binding'] == bound
            and result['model_version'] == entry['version'] and result['source_unchanged'] is True
            and type(result['created_model']) is bool and result['created_version'] is True
            and result['error_code'] is None and type(result['started_ms']) is int
            and type(result['finished_ms']) is int and 0 < result['started_ms'] <= result['finished_ms'] <= 2**53,
            'REGISTRY_RECEIPT_INVALID')
    original = validate_model(result['model'], store_id=bound['store_id'])
    require({k: v for k, v in original.items() if k != 'last_updated_timestamp'} ==
            {k: v for k, v in index['model'].items() if k != 'last_updated_timestamp'}
            and original['last_updated_timestamp'] <= index['model']['last_updated_timestamp'],
            'REGISTRY_RECEIPT_INVALID')
    return deepcopy(result)
