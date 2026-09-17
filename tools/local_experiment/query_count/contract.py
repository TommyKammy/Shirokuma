"""Pure exact COUNT(*) over one retained, schema-compatible Iceberg snapshot.

The request has no projection or row limit. COUNT(*) includes rows with NULL
cells; an optional equality has the existing Filter's non-NULL typed semantics.
The current metadata identity stays truthful when selecting a historical ID.
Public/receipt projections contain an aggregate string, never the predicate
literal or individual rows. A zero count is observed data, not a fallback.

The owner must bind count_sql() once, verify_window() after the query, and close
owned queries/tunnels before publishing project_count()'s candidate. This module
does no I/O and proves neither transport completion nor cleanup. COUNT may scan
the full selected snapshot; the runtime must retain its existing time bounds.
"""
from copy import deepcopy
import re

from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_snapshots import contract as S
from tools.local_experiment.query_filter import contract as F

PROFILE = 'snapshot-count-reader-v4'
SCHEMA_VERSION = 4
ContractError = R.ContractError
require, canonical, digest = R.require, R.canonical, R.digest
CATALOG, SCHEMAS = R.CATALOG, R.SCHEMAS
MAX_INPUT_BYTES, MAX_PUBLIC_BYTES = R.MAX_INPUT_BYTES, R.MAX_PUBLIC_BYTES
MAX_METADATA_BYTES, MAX_LITERAL_BYTES = R.MAX_METADATA_BYTES, F.MAX_LITERAL_BYTES
MAX_COUNT = 2**63 - 1
REQUEST_KEYS = {'command', 'table', 'snapshot_id', 'where'}
RESULT_KEYS = {'schema_version', 'table', 'table_uuid', 'schema_sha256',
               'snapshot_id', 'current_snapshot_id', 'selected_snapshot',
               'count', 'where', 'complete'}
ERROR_CODES = F.ERROR_CODES | frozenset({'COUNT_RESULT_INVALID', 'COUNT_VALUE_INVALID'})
_COUNT = re.compile(r'(?:0|[1-9][0-9]{0,18})\Z')


def _filter_request(value):
    """Adapter for pure predicate validation; never an executable row request."""
    where = value['where']
    require(type(where) is dict and set(where) == {'column', 'literal'}, 'FILTER_INVALID')
    return {'command': 'select', 'table': value['table'], 'columns': [where['column']],
            'limit': 1, 'snapshot_id': value['snapshot_id'], 'where': deepcopy(where)}


def normalize_request(value):
    require(type(value) is dict, 'REQUEST_INVALID')
    R._bounded(value, MAX_INPUT_BYTES, 'INPUT_LIMIT')
    require(set(value) == REQUEST_KEYS, 'REQUEST_KEYS_INVALID')
    require(type(value['command']) is str and value['command'] == 'count', 'COMMAND_INVALID')
    R._table(value['table'])
    if value['snapshot_id'] is not None:
        S.snapshot_id(value['snapshot_id'])
    if value['where'] is not None:
        F.normalize_request(_filter_request(value))
    return deepcopy(value)


def parse_request(raw):
    return normalize_request(R.parse_json(raw, MAX_INPUT_BYTES))


def base_request(value):
    value = normalize_request(value)
    return R.request('describe', table=value['table'])


def discovery_sql(value):
    return R.discovery_sql(base_request(value))


def schema_sql(value):
    return R.schema_sql(base_request(value))


def require_discovered(value, inventory):
    value = normalize_request(value)
    R.require_discovered(base_request(value), inventory)
    return value


def _table_request(value):
    return S.request('snapshots', table=normalize_request(value)['table'])


def project_window(response, value, columns):
    return S.project_window(response, _table_request(value), columns)


def validate_window(window, value, columns=None):
    return S.validate_window(window, _table_request(value), columns)


def verify_window(before, after):
    return S.verify_window(before, after)


def select_snapshot(window, value):
    value = normalize_request(value)
    window = validate_window(window, value)
    selected = value['snapshot_id'] or window['identity']['current_snapshot_id']
    require(selected is not None, 'NO_CURRENT_SNAPSHOT')
    # S.select_snapshot's request adapter needs one schema column, but does not
    # compile or execute a projection. Full-schema compatibility is unchanged.
    anchor = window['identity']['columns'][0]['name']
    selection = S.request('select', table=value['table'], columns=[anchor],
                          limit=1, snapshot_id=selected)
    return S.select_snapshot(window, selection)


def resolve_predicate(value, columns):
    value = normalize_request(value)
    columns = R._columns(columns)
    return None if value['where'] is None else F.resolve_predicate(_filter_request(value), columns)


def predicate_sql(value, columns):
    value = normalize_request(value)
    columns = R._columns(columns)
    return None if value['where'] is None else F.predicate_sql(_filter_request(value), columns)


def count_sql(value, inventory, columns, window):
    value = require_discovered(value, inventory)
    validate_window(window, value, columns)
    snapshot = select_snapshot(window, value)
    predicate = predicate_sql(value, columns)
    sql = ('SELECT COUNT(*) AS "row_count" FROM ' + R.qualified(value['table'])
           + " FOR VERSION AS OF BIGINT '" + snapshot['snapshot_id'] + "'")
    return sql if predicate is None else sql + ' WHERE ' + predicate


def _count_text(value):
    require(type(value) is str and _COUNT.fullmatch(value) and int(value) <= MAX_COUNT,
            'COUNT_VALUE_INVALID')
    return value


def _descriptor(value, columns):
    predicate = resolve_predicate(value, columns)
    return None if predicate is None else {
        'column': predicate['column'], 'type': predicate['type'], 'operator': 'eq'}


def project_count(result, value, columns, window):
    value = normalize_request(value)
    window = validate_window(window, value, columns)
    snapshot = select_snapshot(window, value)
    descriptor = _descriptor(value, columns)
    rows = R._result(result, ['row_count'], ['bigint'], 1)
    require(len(rows) == 1 and type(rows[0][0]) is int and 0 <= rows[0][0] <= MAX_COUNT,
            'COUNT_RESULT_INVALID')
    identity = window['identity']
    public = {'schema_version': SCHEMA_VERSION, 'table': CATALOG + '.' + value['table'],
              'table_uuid': identity['table_uuid'], 'schema_sha256': identity['schema_sha256'],
              'snapshot_id': snapshot['snapshot_id'], 'current_snapshot_id': identity['current_snapshot_id'],
              'selected_snapshot': snapshot, 'count': str(rows[0][0]), 'where': descriptor,
              'complete': True}
    return validate_public(public, value)


def validate_public(public, value=None):
    require(type(public) is dict and set(public) == RESULT_KEYS
            and type(public['schema_version']) is int and public['schema_version'] == SCHEMA_VERSION,
            'PUBLIC_RESULT_INVALID')
    R._bounded(public, MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
    require(type(public['table']) is str and public['table'].startswith(CATALOG + '.'),
            'PUBLIC_RESULT_INVALID')
    table = public['table'][len(CATALOG) + 1:]
    R._table(table)
    R._uuid(public['table_uuid'])
    require(type(public['schema_sha256']) is str and R.SHA256.fullmatch(public['schema_sha256']),
            'PUBLIC_RESULT_INVALID')
    S.snapshot_id(public['current_snapshot_id'])
    S.snapshot_id(public['snapshot_id'])
    record = S._record(public['selected_snapshot'])
    require(record['schema_compatibility'] == 'compatible'
            and record['snapshot_id'] == public['snapshot_id']
            and record['is_current'] == (record['snapshot_id'] == public['current_snapshot_id'])
            and public['complete'] is True, 'PUBLIC_RESULT_INVALID')
    _count_text(public['count'])
    descriptor = public['where']
    if descriptor is not None:
        require(type(descriptor) is dict and set(descriptor) == {'column', 'type', 'operator'}
                and descriptor['operator'] == 'eq', 'FILTER_RESULT_INVALID')
        R._identifier(descriptor['column'])
        kind = R.scalar_type(descriptor['type'])
        require(kind == descriptor['type'] and (kind in ('integer', 'bigint', 'date', 'boolean')
                or R.DECIMAL_TYPE.fullmatch(kind)), 'FILTER_RESULT_INVALID')
    if value is not None:
        value = normalize_request(value)
        require(value['table'] == table and (value['snapshot_id'] or public['current_snapshot_id'])
                == public['snapshot_id'], 'PUBLIC_RESULT_INVALID')
        require((value['where'] is None) == (descriptor is None), 'FILTER_RESULT_INVALID')
        if descriptor is not None:
            require(descriptor['column'] == value['where']['column'], 'FILTER_RESULT_INVALID')
            resolve_predicate(value, [{'name': descriptor['column'], 'type': descriptor['type'], 'nullable': True}])
    return deepcopy(public)


def receipt_projection(public):
    """The validated aggregate is rowless already; no SQL or literal is added."""
    return validate_public(public)
