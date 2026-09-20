"""Read one owned synthetic training run and render its held-out predictions.

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
TEMPLATE_PATH = N.ROOT / 'examples/local_training_review.ipynb'
TEMPLATE_SHA256 = '80b30eb21c3f1ca0d7724801416488e811e7521f87bbdf02450b306d934de74e'
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


def _receipt(value, token, marker):
    require(type(value) is dict and set(value) == RESULT_KEYS and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['result'] == value['phase'] == 'verified'
            and value['token'] == token and value['failure_code'] is None)
    normalize_token(value['run_id']); _uuid(value['store_id']); _sha(value['workload_sha256']); _times(value)
    require(type(value['experiment_id']) is str and re.fullmatch(r'[0-9]{1,20}',value['experiment_id']))
    require(type(value['source_sha256']) is dict and set(value['source_sha256']) == SOURCE_NAMES)
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


def _workload(value):
    require(type(value) is dict and set(value) == {'schema_version','profile','dataset','split','params','model',
            'baseline','metrics','test_predictions','hashes','versions'} and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['profile'] == 'fixed-supervised-regression-v1')
    dataset,split = value['dataset'],value['split']
    require(type(dataset) is dict and set(dataset) == {'feature_names','target_name','rows'}
            and dataset['feature_names'] == ['x1','x2','x3'] and dataset['target_name'] == 'target'
            and type(dataset['rows']) is list and len(dataset['rows']) == 128)
    for row in dataset['rows']:
        require(type(row) is list and len(row) == 4)
        for number in row: _number(number)
    require(type(split) is dict and set(split) == {'seed','train_indices','test_indices'}
            and type(split['seed']) is int and split['seed'] == 1730)
    for key,size in (('train_indices',96),('test_indices',32)):
        require(type(split[key]) is list and len(split[key]) == size
                and all(type(index) is int and 0 <= index < 128 for index in split[key]))
    require(len(set(split['train_indices']+split['test_indices'])) == 128)
    require(value['hashes'] == {'dataset_sha256':DATASET_SHA256,'split_sha256':SPLIT_SHA256}
            and _digest(dataset) == DATASET_SHA256 and _digest(split) == SPLIT_SHA256)
    params = value['params']
    require(type(params) is dict and params == {'algorithm':'Ridge','alpha':1.0,'solver':'svd',
            'fit_intercept':True,'dataset_seed':1729} and type(params['alpha']) is float
            and params['fit_intercept'] is True and type(params['dataset_seed']) is int)
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
    means = [math.fsum(row[j] for row in train)/96 for j in range(4)]
    _close(intercept,means[3]-math.fsum(coefficients[j]*means[j] for j in range(3)))
    for j in range(3):
        gradient = math.fsum((row[j]-means[j])*(math.fsum((row[k]-means[k])*coefficients[k] for k in range(3))
                     -(row[3]-means[3])) for row in train)+coefficients[j]
        require(abs(gradient) <= 1e-8)
    require(type(value['baseline']) is dict and set(value['baseline']) == {'prediction'})
    _close(value['baseline']['prediction'],means[3])
    predict = lambda data:[math.fsum([intercept,*(row[j]*coefficients[j] for j in range(3))]) for row in data]
    predicted = predict(test)
    require(type(value['test_predictions']) is list and len(value['test_predictions']) == 32)
    for actual,expected in zip(value['test_predictions'],predicted): _close(actual,expected)
    _metric_shape(value['metrics'])
    expected = {'train':_metrics([row[3] for row in train],predict(train)),
                'test':_metrics([row[3] for row in test],predicted),
                'baseline_test':_metrics([row[3] for row in test],[means[3]]*32)}
    for group,metrics in expected.items():
        for key,number in metrics.items(): _close(value['metrics'][group][key],number)


def _expected(workload, receipt):
    params = {key:str(value).lower() if type(value) is bool else str(value) for key,value in workload['params'].items()}
    params.update(dataset_sha256=DATASET_SHA256,split_sha256=SPLIT_SHA256,train_rows='96',test_rows='32',
                  feature_names='x1,x2,x3',split_seed='1730',workload=workload['profile'],
                  model_artifact='safe-projection.json',scope='fixed-synthetic-holdout; not general model quality')
    params.update({'version_'+key:value for key,value in workload['versions'].items()})
    tags = {'shirokuma.owner':OWNER,'shirokuma.store_id':receipt['store_id'],'shirokuma.attempt':receipt['token'],
            'shirokuma.workload_sha256':receipt['workload_sha256'],
            'shirokuma.time_semantics':'actual-local-training-and-tracking','mlflow.runName':'fixed-ridge-'+receipt['token'][:8]}
    metrics = {group+'_'+key:value for group,items in workload['metrics'].items() for key,value in items.items()}
    return params,tags,metrics


def _get(path):
    lookup='/api/2.0/mlflow/experiments/get-by-name?'+urllib.parse.urlencode({'experiment_name':EXPERIMENT})
    require(path == lookup or re.fullmatch(r'/api/2.0/mlflow/runs/get\?run_id=[0-9a-f]{32}',path))
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
    require(type(value) is dict and set(value) == {'schema_version','profile','identity','model','metrics','holdout','scope'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE and value['scope'] == SCOPE)
    identity=value['identity']
    require(type(identity) is dict and set(identity) == IDENTITY_KEYS and identity['experiment'] == EXPERIMENT)
    normalize_token(identity['attempt_token']); normalize_token(identity['run_id']); _uuid(identity['store_id'])
    _sha(identity['workload_sha256']); _times(identity)
    require(type(identity['experiment_id']) is str and re.fullmatch(r'[0-9]{1,20}',identity['experiment_id']))
    model=value['model']
    require(type(model) is dict and set(model) == {'algorithm','alpha','solver','feature_names','coefficients','intercept',
            'train_rows','test_rows'} and model['algorithm'] == 'Ridge' and type(model['alpha']) is float and model['alpha'] == 1.0
            and model['solver'] == 'svd' and model['feature_names'] == ['x1','x2','x3']
            and type(model['train_rows']) is int and model['train_rows'] == 96
            and type(model['test_rows']) is int and model['test_rows'] == 32
            and type(model['coefficients']) is list and len(model['coefficients']) == 3)
    for number in [*model['coefficients'],model['intercept']]: _number(number)
    _metric_shape(value['metrics'])
    require(type(value['holdout']) is list and len(value['holdout']) == 32)
    indices=[]
    for row in value['holdout']:
        require(type(row) is dict and set(row) == {'row_index','actual','prediction','baseline_prediction','residual'}
                and type(row['row_index']) is int and 0 <= row['row_index'] < 128)
        for key in ('actual','prediction','baseline_prediction','residual'): _number(row[key])
        _close(row['residual'],row['actual']-row['prediction']); indices.append(row['row_index'])
    require(len(set(indices)) == 32)
    targets=[row['actual'] for row in value['holdout']]
    for group,column in (('test','prediction'),('baseline_test','baseline_prediction')):
        metrics=_metrics(targets,[row[column] for row in value['holdout']])
        for key,number in metrics.items(): _close(value['metrics'][group][key],number)
    require(len({row['baseline_prediction'] for row in value['holdout']}) == 1)
    require(len(N._canonical(value)) <= MAX_FILE)


def _sources(token):
    saved={}
    def read(path,maximum=MAX_FILE):
        raw,signature=_read_file(path,maximum); saved[path]=(raw,signature,maximum)
        return raw
    receipt=N._parse(read(STATE/'training'/token/'result.json',16384))
    marker=N._parse(read(STATE/'store.json',4096)); _receipt(receipt,token,marker)
    workload_raw=read(STATE/'training'/token/'workload.json')
    workload=N._parse(workload_raw); _workload(workload)
    require(workload_raw == N._canonical(workload) and hashlib.sha256(workload_raw).hexdigest() == receipt['workload_sha256'])
    artifact=STATE/'store/artifacts/supervised-training'/receipt['run_id']/'artifacts/safe-projection.json'
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


def _project(receipt, workload):
    holdout=[dict(row_index=index,actual=workload['dataset']['rows'][index][3],prediction=prediction,
        baseline_prediction=workload['baseline']['prediction'],residual=workload['dataset']['rows'][index][3]-prediction)
        for index,prediction in zip(workload['split']['test_indices'],workload['test_predictions'])]
    public=dict(schema_version=1,profile=PROFILE,scope=SCOPE,
        identity=dict(attempt_token=receipt['token'],experiment=EXPERIMENT,**{key:receipt[key] for key in
            ('run_id','experiment_id','store_id','workload_sha256','started_ms','training_finished_ms','finished_ms')}),
        model=dict(algorithm='Ridge',alpha=1.0,solver='svd',feature_names=['x1','x2','x3'],train_rows=96,test_rows=32,
                   **deepcopy(workload['model'])),metrics=deepcopy(workload['metrics']),holdout=holdout)
    validate_public(public)
    return public


def verify_saved_review(value, token):
    """Parent-side source binding after the kernel exits; performs no HTTP."""
    token=normalize_token(token)
    saved,original={},None
    try:
        validate_public(value)
        receipt,workload,_,saved=_sources(token)
        require(value == _project(receipt,workload))
    except BaseException as error:
        original=error
    finally:
        original=_recheck(saved,original)
    if original is not None:
        if not isinstance(original,Exception): raise original
        raise N.NotebookError('NOTEBOOK_TRAINING_REVIEW_CHANGED') from None


def read_training():
    token=normalize_token(os.environ.get(TOKEN_ENV))
    output=N._directory()
    N._write(output/'training-intent.json',{'schema_version':1,'profile':PROFILE,'attempt_token':token})
    saved,original,public = {},None,None
    try:
        receipt,workload,artifact,saved=_sources(token)
        response=_get('/api/2.0/mlflow/experiments/get-by-name?'+urllib.parse.urlencode({'experiment_name':EXPERIMENT}))
        require(type(response) is dict and set(response) == {'experiment'})
        experiment=response['experiment']
        require(experiment['experiment_id'] == receipt['experiment_id'] and experiment['name'] == EXPERIMENT
                and experiment['lifecycle_stage'] == 'active'
                and experiment['artifact_location'] == (STATE/'store/artifacts/supervised-training').as_uri()
                and N._mapping(experiment['tags']) == {'shirokuma.owner':OWNER,'shirokuma.store_id':receipt['store_id']})
        response=_get('/api/2.0/mlflow/runs/get?'+urllib.parse.urlencode({'run_id':receipt['run_id']}))
        require(type(response) is dict and set(response) == {'run'})
        info,data=response['run']['info'],response['run']['data']
        require(info['run_id'] == receipt['run_id'] and info['experiment_id'] == receipt['experiment_id']
                and info['status'] == 'FINISHED' and info['lifecycle_stage'] == 'active'
                and info['artifact_uri'] == artifact.parent.as_uri()
                and type(info['start_time']) is int and info['start_time'] == receipt['started_ms']
                and type(info['end_time']) is int and info['end_time'] == receipt['finished_ms'])
        params,tags,metrics=_expected(workload,receipt)
        require(N._mapping(data['params']) == params and N._mapping(data['tags']) == tags)
        observed=N._mapping(data['metrics'])
        for number in observed.values(): _number(number)
        require(observed == metrics)
        public=_project(receipt,workload)
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
    rows=''.join('<tr><th>'+label+'</th>'+''.join('<td>'+number(value['metrics'][key][metric])+'</td>'
        for metric in ('mae','rmse','r2'))+'</tr>' for key,label in
        (('train','Training · 96 rows'),('test','Holdout · 32 rows'),('baseline_test','Training-mean baseline · 32 holdout rows')))
    predictions=''.join('<tr><th>'+escape(row['row_index'])+'</th>'+''.join('<td>'+number(row[key])+'</td>'
        for key in ('actual','prediction','baseline_prediction','residual'))+'</tr>' for row in value['holdout'])
    return ('<section><h2>Saved Ridge training</h2><p>Run <code>'+escape(value['identity']['run_id'])+
        '</code> · alpha 1 · 3 features</p><p>'+escape(SCOPE)+'</p>'
        '<table><caption>Lower MAE/RMSE is better; R² can be negative.</caption>'
        '<thead><tr><th>Partition</th><th>MAE</th><th>RMSE</th><th>R²</th></tr></thead><tbody>'+rows+'</tbody></table>'
        '<h3>Held-out predictions</h3><p>Residual = actual − prediction. Row index identifies the generated dataset.</p>'
        '<table><thead><tr><th>Row index</th><th>Actual</th><th>Prediction</th><th>Baseline</th><th>Residual</th></tr></thead>'
        '<tbody>'+predictions+'</tbody></table></section>')


def residual_svg(value):
    validate_public(value)
    points=value['holdout']; maximum=max(max(abs(row['residual']) for row in points),1e-12)
    circles=''.join('<circle cx="'+format(48+i*504/31,'.3f')+'" cy="'+format(130-row['residual']*90/maximum,'.3f')+
        '" r="3.5" fill="#176879"><title>'+html.escape('row '+str(row['row_index'])+': '+format(row['residual'],'.6g'))+
        '</title></circle>' for i,row in enumerate(points))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="280" viewBox="0 0 600 280" role="img" '
        'aria-labelledby="residual-title residual-description"><title id="residual-title">Held-out residuals</title>'
        '<desc id="residual-description">Actual minus predicted for 32 rows in fixed holdout order.</desc>'
        '<rect width="600" height="280" fill="white"/><text x="48" y="22" fill="#18333b">Held-out residuals</text>'
        '<path d="M48 40V220H552 M48 130H552" fill="none" stroke="#789299"/>'
        '<text x="8" y="45" font-size="11">'+html.escape(format(maximum,'.3g'))+'</text>'
        '<text x="30" y="134" font-size="11">0</text><text x="8" y="224" font-size="11">−'+
        html.escape(format(maximum,'.3g'))+'</text>'+circles+
        '<text x="48" y="250" fill="#18333b">Fixed holdout order · 32 rows</text></svg>')


def validate_template(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 32768 and hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA256)
    value=N._parse(raw)
    require(value['nbformat'] == 4 and value['nbformat_minor'] == 5 and len(value['cells']) == 6)
    cells=[cell for cell in value['cells'] if cell['cell_type'] == 'code']
    require(len(cells) == 3 and tuple(''.join(cell['source']) for cell in cells) == CODE_CELLS
            and all(cell['outputs'] == [] and cell['execution_count'] is None for cell in cells))
    return value
