"""Two fixed local actors over the existing verified saved-model JSON loader."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import math
import os

from tools.local_experiment.mlflow import predict as P

T = P.T
PROFILE = 'local-ray-saved-prediction-v1'
VERSION = '2.58.0'
COMMIT = 'cec0a091b12675bbf101cf8a350c8706107baee1'
ROW_COUNT = 64
ACTOR_ROWS = 32
SCOPE = ('Two local Ray actors predict the same saved 64 Iceberg rows; no new fit, live table read, '
         'MLflow write, speedup claim, multi-node claim or online Serving.')


class RayPredictionError(ValueError):
    """Fixed codes only; upstream exception messages are never public."""


def require(condition, code='RAY_RESULT_INVALID'):
    if not condition:
        raise RayPredictionError(code)


def keep(first, later):
    return later if first is None or isinstance(first, Exception) and not isinstance(later, Exception) else first


@contextmanager
def saved_batch(token):
    """Hold the shared loader's file fingerprints through actor completion."""
    require(type(token) is str and P.TOKEN.fullmatch(token), 'RAY_ARGUMENTS_INVALID')
    saved, first = {}, None
    try:
        receipt, workload, _, saved = T._sources(token, kind='iceberg')
        review = T._project(receipt, workload, kind='iceberg')
        require(review['identity']['attempt_token'] == token, 'RAY_SOURCE_INVALID')
        rows = workload['dataset']['rows']
        require(type(rows) is list and len(rows) == ROW_COUNT, 'RAY_SOURCE_INVALID')
        request = P.normalize_request({'schema_version': 1, 'rows': [
            dict(zip(P.FEATURES, row[:3])) for row in rows]})
        sequential = P.predict(token, request)
        require(sequential['identity'] == review['identity'] and sequential['lake_source'] == review['lake_source']
                and sequential['model_sha256'] == P.digest(review['model']), 'RAY_SOURCE_INVALID')
        yield request, sequential
    except BaseException as error:
        first = error
    finally:
        first = T._recheck(saved, first)
    if first is not None:
        if not isinstance(first, Exception) or type(first) is RayPredictionError:
            raise first
        raise RayPredictionError('RAY_SOURCE_INVALID') from None


class Predictor:
    """Host-defined actor; receives only the token and bounded JSON features."""
    def __init__(self, token, partition):
        require(type(token) is str and P.TOKEN.fullmatch(token), 'RAY_ARGUMENTS_INVALID')
        require(type(partition) is int and partition in (0, 1))
        self.token, self.partition = token, partition

    def predict(self, request):
        request = P.normalize_request(request)
        require(len(request['rows']) == ACTOR_ROWS)
        result = P.predict(self.token, request)
        return dict(partition=self.partition, pid=os.getpid(), **{
            key: result[key] for key in ('request_sha256', 'identity', 'lake_source', 'model_sha256', 'predictions')})


def validate_partitions(parts, request, sequential):
    require(type(parts) is list and len(parts) == 2)
    predictions, actors = [], []
    keys = {'partition', 'pid', 'request_sha256', 'identity', 'lake_source', 'model_sha256', 'predictions'}
    for index, value in enumerate(parts):
        batch = {'schema_version': 1, 'rows': request['rows'][index*ACTOR_ROWS:(index+1)*ACTOR_ROWS]}
        require(type(value) is dict and set(value) == keys and type(value['partition']) is int
                and value['partition'] == index and type(value['pid']) is int and value['pid'] > 1
                and value['pid'] != os.getpid() and value['request_sha256'] == P.digest(batch)
                and all(value[key] == sequential[key] for key in ('identity', 'lake_source', 'model_sha256')))
        require(type(value['predictions']) is list and len(value['predictions']) == ACTOR_ROWS
                and all(type(number) is float and math.isfinite(number) for number in value['predictions']))
        predictions.extend(value['predictions'])
        actors.append(dict(partition=index, pid=value['pid'], row_offset=index*ACTOR_ROWS,
                           row_count=ACTOR_ROWS, request_sha256=value['request_sha256']))
    require(actors[0]['pid'] != actors[1]['pid'])
    require(predictions == sequential['predictions'], 'RAY_PARITY_FAILED')
    return dict(schema_version=1, profile=PROFILE, training_token=sequential['identity']['attempt_token'],
                identity=deepcopy(sequential['identity']), lake_source=deepcopy(sequential['lake_source']),
                model_sha256=sequential['model_sha256'], request_sha256=P.digest(request),
                predictions=predictions, actors=actors,
                parity=dict(row_count=ROW_COUNT, max_abs_error=0.0,
                            sequential_sha256=P.digest(sequential['predictions']),
                            parallel_sha256=P.digest(predictions)), scope=SCOPE)


def validate_result(value, request, sequential):
    require(type(value) is dict and type(value.get('actors')) is list and len(value['actors']) == 2
            and type(value.get('predictions')) is list and len(value['predictions']) == ROW_COUNT)
    parts = []
    for index, actor in enumerate(value['actors']):
        require(type(actor) is dict and set(actor) == {'partition','pid','row_offset','row_count','request_sha256'}
                and type(actor['row_offset']) is int and actor['row_offset'] == index*ACTOR_ROWS
                and type(actor['row_count']) is int and actor['row_count'] == ACTOR_ROWS)
        parts.append(dict(partition=actor['partition'],pid=actor['pid'],request_sha256=actor['request_sha256'],
            predictions=value['predictions'][index*ACTOR_ROWS:(index+1)*ACTOR_ROWS],
            **{key:value.get(key) for key in ('identity','lake_source','model_sha256')}))
    require(value == validate_partitions(parts,request,sequential))
    return value


def evaluate(token, ray, inspect):
    """No retry, no detached actors, one request per actor, original row order."""
    with saved_batch(token) as (request, sequential):
        actor = ray.remote(num_cpus=1, max_restarts=0, max_task_retries=0)(Predictor)
        actors = [actor.remote(token, index) for index in range(2)]
        refs = [owner.predict.remote({'schema_version': 1,
                'rows': request['rows'][index*ACTOR_ROWS:(index+1)*ACTOR_ROWS]})
                for index, owner in enumerate(actors)]
        parts = ray.get(refs, timeout=60)
        public = validate_partitions(parts, request, sequential)
        inspect([actor['pid'] for actor in public['actors']])
        return public
