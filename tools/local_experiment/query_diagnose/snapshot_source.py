"""Validate a completed snapshot Reader receipt without loading its data again."""
from datetime import datetime
import hashlib
import re

from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_snapshots import contract as S, run as SR
from tools.local_experiment.query_diagnose import contract as D

SOURCE_KEYS = frozenset('''cleanup_verified cloud_calls error_code evidence_token finished_at
invocations limits model_calls observation request request_sha256 result retries schema_version
source_hashes started_at'''.split())
OBSERVATION_KEYS = frozenset('''cleanup_verified error_code identity_after identity_before inventory
queries query_drain request_sha256 runtime selection snapshot_catalog_after snapshot_catalog_before state'''.split())


def require(value):
    R.require(value, 'SOURCE_INVALID')


def validate(read_token, value, digest, transport_validator):
    require(type(value) is dict and set(value) == SOURCE_KEYS)
    require(type(value['schema_version']) is int and value['schema_version'] == 2
            and value['evidence_token'] == read_token and value['result'] == 'passed'
            and type(value['invocations']) is int and value['invocations'] == 1
            and value['cleanup_verified'] is True and value['error_code'] is None)
    require(all(type(value[k]) is int and value[k] == 0 for k in ('model_calls', 'cloud_calls', 'retries')))
    start, end = (datetime.fromisoformat(value[k]) for k in ('started_at', 'finished_at'))
    require(start.tzinfo is not None and end.tzinfo is not None and end >= start)
    request = S.normalize_request(value['request'])
    require(request['command'] == 'select' and value['request_sha256'] == R.digest(request))
    hashes = value['source_hashes']
    require(type(hashes) is dict and set(hashes) == set(SR.sources()) and all(
        type(h) is str and re.fullmatch('[0-9a-f]{64}', h) for h in hashes.values()))
    observation = value['observation']
    require(type(observation) is dict and set(observation) == OBSERVATION_KEYS
            and observation['state'] == 'verified' and observation['error_code'] is None
            and observation['cleanup_verified'] is True and observation['request_sha256'] == R.digest(request))
    windows = [{'schema_version': 2, 'identity': observation['identity_' + phase],
                'snapshots': observation['snapshot_catalog_' + phase]} for phase in ('before', 'after')]
    for window in windows:
        S.validate_window(window, request)
        S.select_snapshot(window, request)
    S.verify_window(*windows)
    queries, runtime, drain = (observation[key] for key in ('queries', 'runtime', 'query_drain'))
    transport_validator(read_token, queries, runtime, drain, runtime['catalog'], S.base_request(request))
    selected = queries['worker'][2]
    source = D.snapshot_source_context(read_token, request, observation['inventory'],
        windows[1]['identity'], observation['selection'], selected['query_id'], digest, windows[1]['snapshots'])
    expected_sql = [S.discovery_sql(request), S.schema_sql(request),
        S.select_sql(request, observation['inventory'], windows[1]['identity']['columns'], windows[1]), S.schema_sql(request)]
    require(all(row['sql_sha256'] == hashlib.sha256(sql.encode()).hexdigest()
                for row, sql in zip(queries['worker'], expected_sql)))
    require(type(selected['row_count']) is int and selected['row_count'] == observation['selection']['fetched_row_count'])
    return source
