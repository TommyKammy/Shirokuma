"""Read one owned saved training run and render its held-out predictions.

Only standard-library validation and two fixed MLflow GETs are used. The JSON
model is arithmetic data, never executable serialization. No training, SDK,
Reader, evaluation experiment, metric-history claim or automatic retry.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import math
import os
from pathlib import Path
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tools.local_experiment.notebook import client as N

STATE = N.ROOT / 'tmp/local-experiment/mlflow-tracking'
EXPERIMENT = OWNER = 'shirokuma-local-supervised-training-v1'
PROFILE = 'training-review-v1'
TOKEN_ENV = 'SHIROKUMA_NOTEBOOK_TRAINING_TOKEN'
KIND_ENV = 'SHIROKUMA_NOTEBOOK_TRAINING_KIND'
TEMPLATE_PATH = N.ROOT / 'examples/local_training_review.ipynb'
TEMPLATE_SHA256 = '80b30eb21c3f1ca0d7724801416488e811e7521f87bbdf02450b306d934de74e'
LAKE_TEMPLATE_PATH = N.ROOT / 'examples/local_lake_training_review.ipynb'
LAKE_TEMPLATE_SHA256 = '9cb30d79ca75cb5440fcfe5cf3c5c7e3fcc735d2fff681e6ed1f1d8158fc1a39'
LAKE_PROFILE = 'lake-training-review-v1'
LAKE_EXPERIMENT = 'shirokuma-local-iceberg-training-v1'
LAKE_FIXTURE_PROFILE = 'fixed-iceberg-regression-source-v1'
LAKE_SCOPE = 'Saved Iceberg snapshot holdout; no live table read, new training or general model-quality claim.'
LAKE_SPLIT_SHA256 = 'f33537d260dcd59987dd2d76869054c01a90984eeac4391fe196adf7615e0af8'
CODE_CELLS = (
    'from IPython.display import HTML, SVG, display\nfrom tools.local_experiment.notebook.training import read_training, render_review, residual_svg',
    'training = read_training()\ndisplay(HTML(render_review(training)))',
    'display(SVG(residual_svg(training)))',
)
SCOPE = 'Saved synthetic holdout; no new training, inference or general model-quality claim.'
# Immutable workload profile v1 fingerprints, not a particular training attempt.
DATASET_SHA256 = '86eb6640f9b0a46a82792c87e1cfee14f6ab17bd8aa8356c63ea70122123d35b'
SPLIT_SHA256 = 'c578d58e7e2c812c39205f4193830bb82c3a08d4d04873bdc524fd21004d7c29'
MAX_FILE = 128 * 1024
SOURCE_NAMES = {'train.py','training_workload.py','runtime.py','import_evaluations.py','pyproject.toml','uv.lock'}
LAKE_SOURCE_NAMES = {'mlflow/'+name for name in SOURCE_NAMES} | {
    'lake_training/tracking.py','lake_training/run.py','lake_training/fixture.py',
    'mlflow/lake_training_workload.py','query_read/contract.py','notebook/client.py','notebook/selection.py'}
LAKE_SOURCE_KEYS = {'source_token','fixture_profile','table','table_uuid','snapshot_id',
                    'schema_sha256','rows_sha256','query_evidence_token','ordering'}
RESULT_KEYS = {'schema_version','result','phase','token','store_id','source_sha256','experiment_id','run_id',
               'started_ms','training_finished_ms','finished_ms','workload_sha256','failure_code'}
IDENTITY_KEYS = {'attempt_token','run_id','experiment_id','experiment','store_id','workload_sha256',
                 'started_ms','training_finished_ms','finished_ms'}


def require(condition):
    if not condition:
        raise N.NotebookError('NOTEBOOK_TRAINING_INVALID')


def normalize_token(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{32}',value))
    return value


def profile(kind='synthetic'):
    """Two fixed saved-run profiles; no path or experiment discovery."""
    require(type(kind) is str and kind in ('synthetic','iceberg'))
    if kind == 'iceberg':
        return dict(profile=LAKE_PROFILE, experiment=LAKE_EXPERIMENT, folder='lake-training',
                    namespace='iceberg-training', workload='fixed-iceberg-regression-v1',
                    prefix='lake-ridge-', rows=64, train_rows=48, test_rows=16,
                    scope=LAKE_SCOPE, source_names=LAKE_SOURCE_NAMES)
    return dict(profile=PROFILE, experiment=EXPERIMENT, folder='training',
                namespace='supervised-training', workload='fixed-supervised-regression-v1',
                prefix='fixed-ridge-', rows=128, train_rows=96, test_rows=32,
                scope=SCOPE, source_names=SOURCE_NAMES)


def template_path(kind='synthetic'):
    profile(kind)
    return LAKE_TEMPLATE_PATH if kind == 'iceberg' else TEMPLATE_PATH


def template_sha256(kind='synthetic'):
    profile(kind)
    return LAKE_TEMPLATE_SHA256 if kind == 'iceberg' else TEMPLATE_SHA256


def _sha(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value))
    return value


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value)
    except (ValueError,AttributeError):
        raise N.NotebookError('NOTEBOOK_TRAINING_INVALID') from None
    return value


def _number(value):
    require(type(value) in (int,float))
    try:
        value = float(value)
    except (OverflowError,ValueError):
        raise N.NotebookError('NOTEBOOK_TRAINING_INVALID') from None
    require(math.isfinite(value) and abs(value) <= 1_000_000)
    return value


def _close(actual, expected):
    require(math.isclose(_number(actual),_number(expected),rel_tol=1e-11,abs_tol=1e-11))


def _digest(value):
    return hashlib.sha256(N._canonical(value)[:-1]).hexdigest()


def _keep(first, later):
    return later if first is None or isinstance(first,Exception) and not isinstance(later,Exception) else first


def _signature(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _file(info, maximum):
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.getuid() and info.st_nlink == 1 and 0 < info.st_size <= maximum)


def _parents(path):
    require(path.is_absolute() and path.is_relative_to(STATE))
    values = []
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode))
        if parent == STATE or parent.is_relative_to(STATE):
            require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid())
        values.append((str(parent),info.st_dev,info.st_ino,info.st_mode,info.st_uid))
    return values


def _read_file(path, maximum=MAX_FILE):
    """Stable owned file read; a racing FIFO or symlink cannot block or redirect."""
    before, parents = path.lstat(), _parents(path)
    _file(before,maximum)
    fd, original, raw = None, None, b''
    try:
        fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(fd); _file(opened,maximum)
        require(_signature(opened) == _signature(before))
        chunks,size = [],0
        while size <= maximum:
            chunk = os.read(fd,maximum+1-size)
            if not chunk: break
            chunks.append(chunk); size += len(chunk)
        raw = b''.join(chunks)
        require(len(raw) <= maximum and _signature(os.fstat(fd)) == _signature(before))
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: original = _keep(original,error)
    if original is not None: raise original
    require(_signature(path.lstat()) == _signature(before) and _parents(path) == parents)
    return raw, (_signature(before),parents)


def _times(value):
    require(all(type(value[key]) is int and 0 < value[key] <= 2**53
                for key in ('started_ms','training_finished_ms','finished_ms')))
    require(value['started_ms'] <= value['training_finished_ms'] <= value['finished_ms'])


def _receipt(value, token, marker, *, kind='synthetic'):
    configured = profile(kind)
    require(type(value) is dict and set(value) == RESULT_KEYS and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['result'] == value['phase'] == 'verified'
            and value['token'] == token and value['failure_code'] is None)
    normalize_token(value['run_id']); _uuid(value['store_id']); _sha(value['workload_sha256']); _times(value)
    require(type(value['experiment_id']) is str and re.fullmatch(r'[0-9]{1,20}',value['experiment_id']))
    require(type(value['source_sha256']) is dict and set(value['source_sha256']) == configured['source_names'])
    for sha in value['source_sha256'].values(): _sha(sha)
    expected = dict(schema_version=1,owner='shirokuma-local-mlflow',mlflow_version='3.16.1',store_id=value['store_id'],
                    backend_uri='sqlite:///'+str(STATE/'store/tracking.sqlite'),
                    artifact_root=str(STATE/'store/artifacts'),url=N.URL)
    require(type(marker) is dict and type(marker.get('schema_version')) is int and marker == expected)


def _metrics(targets, predictions):
    mean = math.fsum(targets)/len(targets)
    variation = math.fsum((item-mean)**2 for item in targets)
    require(variation > 0)
    square = math.fsum((actual-prediction)**2 for actual,prediction in zip(targets,predictions))
    return {'mae':math.fsum(abs(actual-prediction) for actual,prediction in zip(targets,predictions))/len(targets),
            'rmse':math.sqrt(square/len(targets)), 'r2':1.0-square/variation}


def _metric_shape(value):
    require(type(value) is dict and set(value) == {'train','test','baseline_test'})
    for metrics in value.values():
        require(type(metrics) is dict and set(metrics) == {'mae','rmse','r2'})
        for number in metrics.values(): _number(number)
        require(metrics['mae'] >= 0 and metrics['rmse'] >= 0 and metrics['r2'] <= 1)


def _lake_source(value):
    require(type(value) is dict and set(value) == LAKE_SOURCE_KEYS)
    normalize_token(value['source_token']); normalize_token(value['query_evidence_token'])
    require(value['fixture_profile'] == LAKE_FIXTURE_PROFILE
            and value['table'] == 'iceberg.local_experiment.training_'+value['source_token']
            and value['ordering'] == 'unspecified')
    _uuid(value['table_uuid']); _sha(value['schema_sha256']); _sha(value['rows_sha256'])
    require(type(value['snapshot_id']) is str and re.fullmatch(r'[1-9][0-9]{0,18}',value['snapshot_id'])
            and int(value['snapshot_id']) <= 2**63-1)


def _lake_input(value, split):
    """Validate saved typed rows without re-reading a deleted table or fitting."""
    require(type(value) is dict and set(value) == {'fixture_profile','query_evidence_token','observation','split_row_ids'})
    observation = value['observation']
    try:
        N.C.receipt_projection(observation)
    except N.C.ContractError:
        raise N.NotebookError('NOTEBOOK_TRAINING_INVALID') from None
    require(re.fullmatch(r'iceberg\.local_experiment\.training_[0-9a-f]{32}',observation['table'])
            and observation['requested_limit'] == 100
            and observation['row_count'] == observation['fetched_row_count'] == 64
            and observation['truncated'] is False)
    source = dict(source_token=observation['table'].rsplit('_',1)[1],
                  fixture_profile=value['fixture_profile'], query_evidence_token=value['query_evidence_token'],
                  **{key:observation[key] for key in ('table','table_uuid','snapshot_id','schema_sha256','rows_sha256','ordering')})
    _lake_source(source)
    require([column['name'] for column in observation['columns']] == ['row_id','x1','x2','x3','target']
            and [column['type'] for column in observation['columns']] == ['bigint','double','double','double','double'])
    numeric = []
    for row in observation['rows']:
        require(all(cell['value'] is not None for cell in row))
        row_id = int(row[0]['value']); require(0 <= row_id < 64)
        numeric.append((row_id,[_number(float(cell['value'])) for cell in row[1:]]))
    require(len({row_id for row_id,_ in numeric}) == 64)
    row_ids = value['split_row_ids']
    require(type(row_ids) is dict and set(row_ids) == {'train','test'})
    for key,indices in (('train',split['train_indices']),('test',split['test_indices'])):
        require(type(row_ids[key]) is list and all(type(index) is int for index in row_ids[key]) and row_ids[key] == indices)
    numeric.sort(key=lambda row:row[0])
    return dict(feature_names=['x1','x2','x3'],target_name='target',rows=[row for _,row in numeric]),source


def _workload(value, *, kind='synthetic'):
    configured = profile(kind)
    keys = {'schema_version','profile','dataset','split','params','model',
            'baseline','metrics','test_predictions','hashes','versions'}
    if kind == 'iceberg': keys.add('lake_input')
    require(type(value) is dict and set(value) == keys and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['profile'] == configured['workload'])
    dataset,split = value['dataset'],value['split']
    require(type(dataset) is dict and set(dataset) == {'feature_names','target_name','rows'}
            and dataset['feature_names'] == ['x1','x2','x3'] and dataset['target_name'] == 'target'
            and type(dataset['rows']) is list and len(dataset['rows']) == configured['rows'])
    for row in dataset['rows']:
        require(type(row) is list and len(row) == 4)
        for number in row: _number(number)
    require(type(split) is dict and set(split) == {'seed','train_indices','test_indices'}
            and type(split['seed']) is int and split['seed'] == 1730)
    for key,size in (('train_indices',configured['train_rows']),('test_indices',configured['test_rows'])):
        require(type(split[key]) is list and len(split[key]) == size
                and all(type(index) is int and 0 <= index < configured['rows'] for index in split[key]))
    require(len(set(split['train_indices']+split['test_indices'])) == configured['rows'])
    dataset_sha, split_sha = DATASET_SHA256, SPLIT_SHA256
    if kind == 'iceberg':
        expected,_ = _lake_input(value['lake_input'],split)
        require(N._canonical(dataset) == N._canonical(expected))
        dataset_sha,split_sha = _digest(dataset),LAKE_SPLIT_SHA256
    require(value['hashes'] == {'dataset_sha256':dataset_sha,'split_sha256':split_sha}
            and _digest(dataset) == dataset_sha and _digest(split) == split_sha)
    params = value['params']
    expected_params = {'algorithm':'Ridge','alpha':1.0,'solver':'svd','fit_intercept':True}
    if kind == 'synthetic': expected_params['dataset_seed'] = 1729
    require(type(params) is dict and params == expected_params and type(params['alpha']) is float
            and params['fit_intercept'] is True)
    if kind == 'synthetic': require(type(params['dataset_seed']) is int)
    versions = value['versions']
    require(type(versions) is dict and set(versions) == {'python','numpy','scikit_learn'}
            and all(type(version) is str and len(version) <= 64
                    and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,32}',version)
                    for version in versions.values()))
    model = value['model']
    require(type(model) is dict and set(model) == {'coefficients','intercept'}
            and type(model['coefficients']) is list and len(model['coefficients']) == 3)
    coefficients = [_number(item) for item in model['coefficients']]; intercept = _number(model['intercept'])
    rows = dataset['rows']; train = [rows[i] for i in split['train_indices']]; test = [rows[i] for i in split['test_indices']]
    means = [math.fsum(row[j] for row in train)/configured['train_rows'] for j in range(4)]
    _close(intercept,means[3]-math.fsum(coefficients[j]*means[j] for j in range(3)))
    for j in range(3):
        gradient = math.fsum((row[j]-means[j])*(math.fsum((row[k]-means[k])*coefficients[k] for k in range(3))
                     -(row[3]-means[3])) for row in train)+coefficients[j]
        require(abs(gradient) <= 1e-8)
    require(type(value['baseline']) is dict and set(value['baseline']) == {'prediction'})
    _close(value['baseline']['prediction'],means[3])
    predict = lambda data:[math.fsum([intercept,*(row[j]*coefficients[j] for j in range(3))]) for row in data]
    predicted = predict(test)
    require(type(value['test_predictions']) is list and len(value['test_predictions']) == configured['test_rows'])
    for actual,expected in zip(value['test_predictions'],predicted): _close(actual,expected)
    _metric_shape(value['metrics'])
    expected = {'train':_metrics([row[3] for row in train],predict(train)),
                'test':_metrics([row[3] for row in test],predicted),
                'baseline_test':_metrics([row[3] for row in test],[means[3]]*configured['test_rows'])}
    for group,metrics in expected.items():
        for key,number in metrics.items(): _close(value['metrics'][group][key],number)


def _expected(workload, receipt, *, kind='synthetic'):
    configured = profile(kind)
    params = {key:str(value).lower() if type(value) is bool else str(value) for key,value in workload['params'].items()}
    params.update(**workload['hashes'],train_rows=str(configured['train_rows']),test_rows=str(configured['test_rows']),
                  feature_names='x1,x2,x3',split_seed='1730',workload=workload['profile'],
                  model_artifact='safe-projection.json',scope='fixed-synthetic-holdout; not general model quality')
    params.update({'version_'+key:value for key,value in workload['versions'].items()})
    if kind == 'iceberg':
        _,source = _lake_input(workload['lake_input'],workload['split'])
        params.update(scope='fixed-iceberg-holdout; not general model quality',
            lake_table=source['table'],lake_table_uuid=source['table_uuid'],lake_snapshot_id=source['snapshot_id'],
            lake_schema_sha256=source['schema_sha256'],lake_rows_sha256=source['rows_sha256'],
            query_evidence_token=source['query_evidence_token'],source_token=source['source_token'],
            fixture_profile=source['fixture_profile'])
    tags = {'shirokuma.owner':configured['experiment'],'shirokuma.store_id':receipt['store_id'],'shirokuma.attempt':receipt['token'],
            'shirokuma.workload_sha256':receipt['workload_sha256'],
            'shirokuma.time_semantics':'actual-local-training-and-tracking','mlflow.runName':configured['prefix']+receipt['token'][:8]}
    metrics = {group+'_'+key:value for group,items in workload['metrics'].items() for key,value in items.items()}
    return params,tags,metrics


def _get(path):
    lookups={'/api/2.0/mlflow/experiments/get-by-name?'+urllib.parse.urlencode({'experiment_name':name})
             for name in (EXPERIMENT,LAKE_EXPERIMENT)}
    require(path in lookups or re.fullmatch(r'/api/2.0/mlflow/runs/get\?run_id=[0-9a-f]{32}',path))
    response,original,raw = None,None,b''
    try:
        request=urllib.request.Request(N.URL+path,method='GET')
        response=urllib.request.build_opener(urllib.request.ProxyHandler({}),N._NoRedirect()).open(request,timeout=10)
        require(response.status == 200)
        raw=response.read(N.MAX_HTTP+1); require(len(raw) <= N.MAX_HTTP)
    except BaseException as error:
        original=error
        if isinstance(error,urllib.error.HTTPError): response=error
    finally:
        if response is not None:
            try: response.close()
            except BaseException as error: original=_keep(original,error)
    if original is not None: raise original
    return N._parse(raw)


def validate_public(value):
    require(type(value) is dict and value.get('profile') in (PROFILE,LAKE_PROFILE))
    kind = 'iceberg' if value['profile'] == LAKE_PROFILE else 'synthetic'
    configured = profile(kind)
    keys = {'schema_version','profile','identity','model','metrics','holdout','scope'}
    if kind == 'iceberg': keys.add('lake_source')
    require(set(value) == keys
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['scope'] == configured['scope'])
    if kind == 'iceberg': _lake_source(value['lake_source'])
    identity=value['identity']
    require(type(identity) is dict and set(identity) == IDENTITY_KEYS and identity['experiment'] == configured['experiment'])
    normalize_token(identity['attempt_token']); normalize_token(identity['run_id']); _uuid(identity['store_id'])
    _sha(identity['workload_sha256']); _times(identity)
    require(type(identity['experiment_id']) is str and re.fullmatch(r'[0-9]{1,20}',identity['experiment_id']))
    model=value['model']
    require(type(model) is dict and set(model) == {'algorithm','alpha','solver','feature_names','coefficients','intercept',
            'train_rows','test_rows'} and model['algorithm'] == 'Ridge' and type(model['alpha']) is float and model['alpha'] == 1.0
            and model['solver'] == 'svd' and model['feature_names'] == ['x1','x2','x3']
            and type(model['train_rows']) is int and model['train_rows'] == configured['train_rows']
            and type(model['test_rows']) is int and model['test_rows'] == configured['test_rows']
            and type(model['coefficients']) is list and len(model['coefficients']) == 3)
    for number in [*model['coefficients'],model['intercept']]: _number(number)
    _metric_shape(value['metrics'])
    require(type(value['holdout']) is list and len(value['holdout']) == configured['test_rows'])
    indices=[]
    for row in value['holdout']:
        require(type(row) is dict and set(row) == {'row_index','actual','prediction','baseline_prediction','residual'}
                and type(row['row_index']) is int and 0 <= row['row_index'] < configured['rows'])
        for key in ('actual','prediction','baseline_prediction','residual'): _number(row[key])
        _close(row['residual'],row['actual']-row['prediction']); indices.append(row['row_index'])
    require(len(set(indices)) == configured['test_rows'])
    targets=[row['actual'] for row in value['holdout']]
    for group,column in (('test','prediction'),('baseline_test','baseline_prediction')):
        metrics=_metrics(targets,[row[column] for row in value['holdout']])
        for key,number in metrics.items(): _close(value['metrics'][group][key],number)
    require(len({row['baseline_prediction'] for row in value['holdout']}) == 1)
    require(len(N._canonical(value)) <= MAX_FILE)


def _sources(token, *, kind='synthetic'):
    configured = profile(kind)
    saved={}
    def read(path,maximum=MAX_FILE):
        raw,signature=_read_file(path,maximum); saved[path]=(raw,signature,maximum)
        return raw
    receipt=N._parse(read(STATE/configured['folder']/token/'result.json',16384))
    marker=N._parse(read(STATE/'store.json',4096)); _receipt(receipt,token,marker,kind=kind)
    workload_raw=read(STATE/configured['folder']/token/'workload.json')
    workload=N._parse(workload_raw); _workload(workload,kind=kind)
    require(workload_raw == N._canonical(workload) and hashlib.sha256(workload_raw).hexdigest() == receipt['workload_sha256'])
    artifact=STATE/'store/artifacts'/configured['namespace']/receipt['run_id']/'artifacts/safe-projection.json'
    require(read(artifact) == workload_raw)
    return receipt,workload,artifact,saved


def _recheck(saved, original):
    for path,(raw,signature,maximum) in saved.items():
        try:
            current,current_signature=_read_file(path,maximum)
            require(current == raw and current_signature == signature)
        except BaseException as error:
            original=_keep(original,error)
    return original


def _project(receipt, workload, *, kind='synthetic'):
    configured = profile(kind)
    holdout=[dict(row_index=index,actual=workload['dataset']['rows'][index][3],prediction=prediction,
        baseline_prediction=workload['baseline']['prediction'],residual=workload['dataset']['rows'][index][3]-prediction)
        for index,prediction in zip(workload['split']['test_indices'],workload['test_predictions'])]
    public=dict(schema_version=1,profile=configured['profile'],scope=configured['scope'],
        identity=dict(attempt_token=receipt['token'],experiment=configured['experiment'],**{key:receipt[key] for key in
            ('run_id','experiment_id','store_id','workload_sha256','started_ms','training_finished_ms','finished_ms')}),
        model=dict(algorithm='Ridge',alpha=1.0,solver='svd',feature_names=['x1','x2','x3'],
                   train_rows=configured['train_rows'],test_rows=configured['test_rows'],
                   **deepcopy(workload['model'])),metrics=deepcopy(workload['metrics']),holdout=holdout)
    if kind == 'iceberg': _,public['lake_source'] = _lake_input(workload['lake_input'],workload['split'])
    validate_public(public)
    return public


def verify_saved_review(value, token, *, kind='synthetic'):
    """Parent-side source binding after the kernel exits; performs no HTTP."""
    token=normalize_token(token)
    saved,original={},None
    try:
        validate_public(value)
        require(value['profile'] == profile(kind)['profile'])
        receipt,workload,_,saved=_sources(token,kind=kind)
        require(value == _project(receipt,workload,kind=kind))
    except BaseException as error:
        original=error
    finally:
        original=_recheck(saved,original)
    if original is not None:
        if not isinstance(original,Exception): raise original
        raise N.NotebookError('NOTEBOOK_TRAINING_REVIEW_CHANGED') from None


def read_training(*, kind='synthetic'):
    configured=profile(kind)
    require(os.environ.get(KIND_ENV,'synthetic') == kind)
    token=normalize_token(os.environ.get(TOKEN_ENV))
    output=N._directory()
    intent={'schema_version':1,'profile':configured['profile'],'attempt_token':token}
    if kind == 'iceberg': intent['kind']='iceberg'
    N._write(output/'training-intent.json',intent)
    saved,original,public = {},None,None
    try:
        receipt,workload,artifact,saved=_sources(token,kind=kind)
        response=_get('/api/2.0/mlflow/experiments/get-by-name?'+urllib.parse.urlencode({'experiment_name':configured['experiment']}))
        require(type(response) is dict and set(response) == {'experiment'})
        experiment=response['experiment']
        require(experiment['experiment_id'] == receipt['experiment_id'] and experiment['name'] == configured['experiment']
                and experiment['lifecycle_stage'] == 'active'
                and experiment['artifact_location'] == (STATE/'store/artifacts'/configured['namespace']).as_uri()
                and N._mapping(experiment['tags']) == {'shirokuma.owner':configured['experiment'],'shirokuma.store_id':receipt['store_id']})
        response=_get('/api/2.0/mlflow/runs/get?'+urllib.parse.urlencode({'run_id':receipt['run_id']}))
        require(type(response) is dict and set(response) == {'run'})
        info,data=response['run']['info'],response['run']['data']
        require(info['run_id'] == receipt['run_id'] and info['experiment_id'] == receipt['experiment_id']
                and info['status'] == 'FINISHED' and info['lifecycle_stage'] == 'active'
                and info['artifact_uri'] == artifact.parent.as_uri()
                and type(info['start_time']) is int and info['start_time'] == receipt['started_ms']
                and type(info['end_time']) is int and info['end_time'] == receipt['finished_ms'])
        params,tags,metrics=_expected(workload,receipt,kind=kind)
        require(N._mapping(data['params']) == params and N._mapping(data['tags']) == tags)
        observed=N._mapping(data['metrics'])
        for number in observed.values(): _number(number)
        require(observed == metrics)
        public=_project(receipt,workload,kind=kind)
        require(os.environ.get(KIND_ENV,'synthetic') == kind and os.environ.get(TOKEN_ENV) == token)
    except BaseException as error:
        original=error
    finally:
        original=_recheck(saved,original)
    if original is not None:
        if not isinstance(original,Exception): raise original
        raise N.NotebookError('NOTEBOOK_TRAINING_READ_FAILED') from None
    N._write(output/'training-review.json',public)
    return public


def render_review(value):
    validate_public(value)
    escape=lambda item:html.escape(str(item),quote=True)
    number=lambda item:escape(format(item,'.6g'))
    train_rows,test_rows=value['model']['train_rows'],value['model']['test_rows']
    rows=''.join('<tr><th>'+label+'</th>'+''.join('<td>'+number(value['metrics'][key][metric])+'</td>'
        for metric in ('mae','rmse','r2'))+'</tr>' for key,label in
        (('train',f'Training · {train_rows} rows'),('test',f'Holdout · {test_rows} rows'),
         ('baseline_test',f'Training-mean baseline · {test_rows} holdout rows')))
    predictions=''.join('<tr><th>'+escape(row['row_index'])+'</th>'+''.join('<td>'+number(row[key])+'</td>'
        for key in ('actual','prediction','baseline_prediction','residual'))+'</tr>' for row in value['holdout'])
    source=''
    row_scope='Row index identifies the generated dataset.'
    if value['profile'] == LAKE_PROFILE:
        labels=(('table','Table'),('table_uuid','Table UUID'),('snapshot_id','Saved snapshot'),
                ('schema_sha256','Schema SHA-256'),('rows_sha256','Original Reader rows SHA-256'),
                ('query_evidence_token','Reader evidence'),('source_token','Source attempt'),
                ('fixture_profile','Fixture profile'),('ordering','Original Reader order'))
        source='<h3>Saved Iceberg source</h3><dl>'+''.join('<dt>'+label+'</dt><dd><code>'+
            escape(value['lake_source'][key])+'</code></dd>' for key,label in labels)+'</dl>'
        row_scope='Row index is the saved row_id; training used row_id order before the fixed split.'
    return ('<section><h2>Saved Ridge training</h2><p>Run <code>'+escape(value['identity']['run_id'])+
        '</code> · alpha 1 · 3 features</p><p>'+escape(value['scope'])+'</p>'+source+
        '<table><caption>Lower MAE/RMSE is better; R² can be negative.</caption>'
        '<thead><tr><th>Partition</th><th>MAE</th><th>RMSE</th><th>R²</th></tr></thead><tbody>'+rows+'</tbody></table>'
        '<h3>Held-out predictions</h3><p>Residual = actual − prediction. '+row_scope+'</p>'
        '<table><thead><tr><th>Row index</th><th>Actual</th><th>Prediction</th><th>Baseline</th><th>Residual</th></tr></thead>'
        '<tbody>'+predictions+'</tbody></table></section>')


def residual_svg(value):
    validate_public(value)
    points=value['holdout']; maximum=max(max(abs(row['residual']) for row in points),1e-12)
    circles=''.join('<circle cx="'+format(48+i*504/(len(points)-1),'.3f')+'" cy="'+format(130-row['residual']*90/maximum,'.3f')+
        '" r="3.5" fill="#176879"><title>'+html.escape('row '+str(row['row_index'])+': '+format(row['residual'],'.6g'))+
        '</title></circle>' for i,row in enumerate(points))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="280" viewBox="0 0 600 280" role="img" '
        'aria-labelledby="residual-title residual-description"><title id="residual-title">Held-out residuals</title>'
        '<desc id="residual-description">Actual minus predicted for '+str(len(points))+' rows in fixed holdout order.</desc>'
        '<rect width="600" height="280" fill="white"/><text x="48" y="22" fill="#18333b">Held-out residuals</text>'
        '<path d="M48 40V220H552 M48 130H552" fill="none" stroke="#789299"/>'
        '<text x="8" y="45" font-size="11">'+html.escape(format(maximum,'.3g'))+'</text>'
        '<text x="30" y="134" font-size="11">0</text><text x="8" y="224" font-size="11">−'+
        html.escape(format(maximum,'.3g'))+'</text>'+circles+
        '<text x="48" y="250" fill="#18333b">Fixed holdout order · '+str(len(points))+' rows</text></svg>')


def validate_template(raw, *, kind='synthetic'):
    expected=template_sha256(kind)
    code_cells=CODE_CELLS
    if kind == 'iceberg':
        code_cells=(CODE_CELLS[0],'training = read_training(kind="iceberg")\ndisplay(HTML(render_review(training)))',CODE_CELLS[2])
    require(type(raw) is bytes and 0 < len(raw) <= 32768 and hashlib.sha256(raw).hexdigest() == expected)
    value=N._parse(raw)
    require(value['nbformat'] == 4 and value['nbformat_minor'] == 5 and len(value['cells']) == 6)
    cells=[cell for cell in value['cells'] if cell['cell_type'] == 'code']
    require(len(cells) == 3 and tuple(''.join(cell['source']) for cell in cells) == code_cells
            and all(cell['outputs'] == [] and cell['execution_count'] is None for cell in cells))
    return value
