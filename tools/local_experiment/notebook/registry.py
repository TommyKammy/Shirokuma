"""Explicit local Registry version to saved Ridge prediction, with no writes."""
from __future__ import annotations

from copy import deepcopy
from html import escape
import math

from tools.local_experiment.mlflow import predict as P
from tools.local_experiment.mlflow import registry as R
from tools.local_experiment.mlflow import registry_contract as C
from tools.local_experiment.notebook.client import NotebookError

PROFILE = 'notebook-registered-ridge-prediction-v1'
MODEL_NAME = C.MODEL_NAME


def require(value, code='NOTEBOOK_REGISTRY_RESULT_INVALID'):
    if not value:
        raise NotebookError(code)


def validate_public(value):
    """Validate the bound display projection without a database or model read."""
    try:
        require(type(value) is dict and set(value) == {
            'schema_version', 'profile', 'result', 'registry', 'request', 'prediction'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE and value['result'] == 'passed')
        registry = value['registry']
        require(type(registry) is dict and set(registry) == {'name', 'version', 'binding'}
                and registry['name'] == MODEL_NAME and type(registry['version']) is str
                and C.version(registry['version']) == registry['version'])
        bound = C.validate_binding(registry['binding'])
        request = P.normalize_request(value['request'])
        prediction = value['prediction']
        require(type(prediction) is dict and set(prediction) == {
            'schema_version', 'profile', 'result', 'request_sha256', 'identity',
            'lake_source', 'model_sha256', 'predictions', 'error_code', 'scope'}
            and type(prediction['schema_version']) is int and prediction['schema_version'] == 1
            and prediction['profile'] == P.PROFILE and prediction['result'] == 'passed'
            and prediction['error_code'] is None and prediction['scope'] == P.SCOPE
            and prediction['request_sha256'] == P.digest(request)
            and prediction['model_sha256'] == bound['model_sha256'])
        identity = prediction['identity']
        require(type(identity) is dict and all(identity.get(key) == bound[bound_key]
            for key, bound_key in (
                ('attempt_token', 'training_token'), ('run_id', 'run_id'),
                ('experiment_id', 'experiment_id'), ('store_id', 'store_id'),
                ('workload_sha256', 'workload_sha256'))))
        predictions = prediction['predictions']
        require(type(predictions) is list and len(predictions) == len(request['rows'])
                and all(type(number) in (int, float) and math.isfinite(number)
                        for number in predictions))
        return deepcopy(value)
    except Exception:
        raise NotebookError('NOTEBOOK_REGISTRY_RESULT_INVALID') from None


def predict_registered(name, version, request):
    """Resolve an explicit version with SQLite reads, then use the saved predictor."""
    try:
        require(name == MODEL_NAME, 'NOTEBOOK_REGISTRY_ARGUMENTS_INVALID')
        version = C.version(version)
        request = P.normalize_request(request)
    except Exception:
        raise NotebookError('NOTEBOOK_REGISTRY_ARGUMENTS_INVALID') from None
    try:
        resolved = R.resolve(name, version)
        require(type(resolved) is dict and type(resolved.get('schema_version')) is int
                and resolved['schema_version'] == 1
                and resolved.get('profile') == C.PROFILE and resolved.get('result') == 'verified'
                and resolved.get('observation') == 'sqlite-read-only'
                and resolved.get('model_name') == name and resolved.get('version') == version)
        bound = C.validate_binding(resolved['binding'])
        prediction = P.predict(bound['training_token'], deepcopy(request))
        return validate_public(dict(schema_version=1, profile=PROFILE, result='passed',
            registry=dict(name=name, version=version, binding=bound), request=request,
            prediction=prediction))
    except NotebookError:
        raise
    except Exception:
        raise NotebookError('NOTEBOOK_REGISTRY_UNAVAILABLE') from None


def render_prediction(value):
    """Render an escaped, compact input/prediction table using the Notebook theme."""
    value = validate_public(value)
    registry, prediction = value['registry'], value['prediction']
    number = lambda item: escape(str(item))
    rows = ''.join('<tr><th scope="row">' + str(index + 1) + '</th>'
        + ''.join('<td>' + number(row[feature]) + '</td>' for feature in P.FEATURES)
        + '<td>' + number(predicted) + '</td></tr>'
        for index, (row, predicted) in enumerate(zip(value['request']['rows'], prediction['predictions'])))
    bound = registry['binding']
    return ('<section><h3>登録モデルの予測</h3><p>モデル <code>'
        + escape(registry['name']) + '</code> / version <code>' + escape(registry['version'])
        + '</code></p><div role="region" aria-label="入力と予測値" tabindex="0" '
          'style="max-width:100%;overflow-x:auto"><table><caption>入力と予測値</caption>'
          '<thead><tr><th scope="col">行</th>'
        + ''.join('<th scope="col">' + escape(feature) + '</th>' for feature in P.FEATURES)
        + '<th scope="col">予測値</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
          '<details><summary>予測に使ったモデル</summary><dl>'
        + ''.join('<dt>' + label + '</dt><dd><code style="overflow-wrap:anywhere">'
                  + escape(bound[key]) + '</code></dd>' for label, key in (
                      ('学習 ID', 'training_token'), ('学習 run ID', 'run_id'),
                      ('モデル SHA-256', 'model_sha256')))
        + '</dl></details><p>保存済みモデルによるローカル予測です。</p></section>')
