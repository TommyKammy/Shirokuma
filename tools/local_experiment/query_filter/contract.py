"""One schema-bound non-NULL equality; existing Reader v1/v2 stay unchanged.

Literal values are private request evidence and appear in compiled Trino SQL.
Public metadata/model inputs do not include them. Explicit result rows may.
The metadata window is the existing snapshot v2 shape; result/source profile is
v3 and never rewrites current identity to pretend it is a historical selection.
"""
from copy import deepcopy
from datetime import date
import re

from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_snapshots import contract as S

PROFILE = 'filtered-snapshot-reader-v3'
ContractError = R.ContractError
require, canonical, digest = R.require, R.canonical, R.digest
MAX_INPUT_BYTES, MAX_PUBLIC_BYTES = R.MAX_INPUT_BYTES, R.MAX_PUBLIC_BYTES
MAX_METADATA_BYTES, MAX_LITERAL_BYTES = R.MAX_METADATA_BYTES, 128
RESULT_KEYS = S.RESULT_KEYS | {'where'}
ERROR_CODES = S.ERROR_CODES | frozenset({'FILTER_INVALID', 'FILTER_COLUMN_INVALID',
    'FILTER_TYPE_UNSUPPORTED', 'FILTER_VALUE_INVALID', 'FILTER_RESULT_INVALID'})
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')
_INTEGER = re.compile(r'-?(?:0|[1-9][0-9]*)\Z')
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')


def normalize_request(value):
    require(type(value) is dict and set(value) == {
        'command', 'table', 'columns', 'limit', 'snapshot_id', 'where'}, 'REQUEST_KEYS_INVALID')
    R._bounded(value, MAX_INPUT_BYTES, 'INPUT_LIMIT')
    require(value['command'] == 'select', 'COMMAND_INVALID')
    R.normalize_request({k:value[k] for k in ('command', 'table', 'columns', 'limit')})
    if value['snapshot_id'] is not None:
        S.snapshot_id(value['snapshot_id'])
    where = value['where']
    require(type(where) is dict and set(where) == {'column', 'literal'}, 'FILTER_INVALID')
    R._identifier(where['column'])
    text = where['literal']
    require(type(text) is str and 0 < len(text) <= MAX_LITERAL_BYTES
            and text.isascii(), 'FILTER_INVALID')
    require(text in ('true', 'false') or _NUMBER.fullmatch(text) or _DATE.fullmatch(text), 'FILTER_INVALID')
    if _DATE.fullmatch(text):
        try:
            require(date.fromisoformat(text).isoformat() == text, 'FILTER_INVALID')
        except ValueError:
            raise ContractError('FILTER_INVALID') from None
    return deepcopy(value)


def parse_request(raw):
    # Reuse duplicate-key/non-finite/UTF-8 parsing without widening v1 requests.
    return normalize_request(R.parse_json(raw, MAX_INPUT_BYTES))


def base_request(value):
    value = normalize_request(value)
    return R.normalize_request({k:value[k] for k in ('command', 'table', 'columns', 'limit')})


def discovery_sql(value):
    return R.discovery_sql(base_request(value))


def schema_sql(value):
    return R.schema_sql(base_request(value))


def require_discovered(value, inventory):
    R.require_discovered(base_request(value), inventory)
    return normalize_request(value)


def selected_columns(value, columns):
    return R.selected_columns(base_request(value), columns)


def _table_request(value):
    value = normalize_request(value)
    return S.request('snapshots', table=value['table'])


def project_window(response, value, columns):
    return S.project_window(response, _table_request(value), columns)


def validate_window(window, value, columns=None):
    return S.validate_window(window, _table_request(value), columns)


def verify_window(before, after):
    return S.verify_window(before, after)


def _snapshot_request(value, window):
    value = normalize_request(value)
    window = validate_window(window, value)
    selected = value['snapshot_id'] or window['identity']['current_snapshot_id']
    require(selected is not None, 'NO_CURRENT_SNAPSHOT')
    return S.request('select', table=value['table'], columns=value['columns'],
                     limit=value['limit'], snapshot_id=selected)


def select_snapshot(window, value):
    return S.select_snapshot(window, _snapshot_request(value, window))


def resolve_predicate(value, columns):
    """Return canonical {column,type,value}; no float conversion or rounding."""
    value = normalize_request(value)
    columns = R._columns(columns)
    found = [c for c in columns if c['name'] == value['where']['column']]
    require(len(found) == 1, 'FILTER_COLUMN_INVALID')
    spec, text = found[0], value['where']['literal']
    kind = spec['type']
    require(kind in ('integer', 'bigint', 'boolean', 'date') or R.DECIMAL_TYPE.fullmatch(kind),
            'FILTER_TYPE_UNSUPPORTED')
    try:
        if kind in ('integer', 'bigint'):
            require(_INTEGER.fullmatch(text), 'FILTER_VALUE_INVALID')
            raw = int(text)
        elif kind == 'boolean':
            require(text in ('true', 'false'), 'FILTER_VALUE_INVALID')
            raw = text == 'true'
        else:
            raw = text
        cell = R._cell(raw, spec)
    except (R.ContractError, ValueError, OverflowError):
        raise ContractError('FILTER_VALUE_INVALID') from None
    require(cell['value'] is not None, 'FILTER_VALUE_INVALID')
    return {'column':spec['name'], 'type':kind, 'value':cell['value']}


def predicate_sql(value, columns):
    """Return a condition fragment without WHERE; all text is compiled here."""
    predicate = resolve_predicate(value, columns)
    kind, text = predicate['type'], predicate['value']
    if kind == 'boolean':
        literal = 'TRUE' if text else 'FALSE'
    elif kind in ('integer', 'bigint', 'date'):
        literal = kind.upper() + " '" + text + "'"
    else:
        literal = "CAST('" + text + "' AS " + kind.upper() + ')'
    return R.quote(predicate['column']) + ' = ' + literal


def select_sql(value, inventory, columns, window):
    value = require_discovered(value, inventory)
    selected = selected_columns(value, columns)
    validate_window(window, value, columns)
    snapshot = select_snapshot(window, value)
    return ('SELECT ' + ', '.join(R.quote(c['name']) for c in selected) + ' FROM '
            + R.qualified(value['table']) + " FOR VERSION AS OF BIGINT '" + snapshot['snapshot_id']
            + "' WHERE " + predicate_sql(value, columns) + f" LIMIT {value['limit'] + 1}")


def project_rows(result, value, columns, window):
    predicate = resolve_predicate(value, columns)
    public = S.project_rows(result, _snapshot_request(value, window), columns, window)
    # Validate even the limit+1 sentinel row, which is omitted from public rows.
    names = [c['name'] for c in public['columns']]
    if predicate['column'] in names:
        index = names.index(predicate['column'])
        for row in result['rows']:
            require(R._cell(row[index], public['columns'][index])['value'] == predicate['value'],
                    'FILTER_RESULT_INVALID')
    public.update(schema_version=3, where={'column':predicate['column'], 'type':predicate['type'], 'operator':'eq'})
    return validate_public(public, value)


def validate_public(public, value=None):
    require(type(public) is dict and set(public) == RESULT_KEYS
            and type(public.get('schema_version')) is int and public['schema_version'] == 3,
            'PUBLIC_RESULT_INVALID')
    R._bounded(public, MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
    descriptor = public['where']
    require(type(descriptor) is dict and set(descriptor) == {'column', 'type', 'operator'}
            and descriptor['operator'] == 'eq', 'FILTER_RESULT_INVALID')
    R._identifier(descriptor['column'])
    kind = R.scalar_type(descriptor['type'])
    require(kind == descriptor['type'] and (kind in ('integer', 'bigint', 'date', 'boolean')
            or R.DECIMAL_TYPE.fullmatch(kind)), 'FILTER_RESULT_INVALID')
    legacy = {k:deepcopy(v) for k,v in public.items() if k != 'where'}
    legacy['schema_version'] = 2
    expected = None
    if value is not None:
        value = normalize_request(value)
        require(descriptor['column'] == value['where']['column'], 'FILTER_RESULT_INVALID')
        selected = value['snapshot_id'] or public['current_snapshot_id']
        expected = S.request('select', table=value['table'], columns=value['columns'],
                             limit=value['limit'], snapshot_id=selected)
    S.validate_public(legacy, expected)
    selected_columns_by_name = {c['name']:c for c in public['columns']}
    if descriptor['column'] in selected_columns_by_name:
        require(selected_columns_by_name[descriptor['column']]['type'] == kind, 'FILTER_RESULT_INVALID')
    if value is not None:
        predicate = resolve_predicate(value, [{'name':descriptor['column'], 'type':kind, 'nullable':True}])
        names = [c['name'] for c in public['columns']]
        if descriptor['column'] in names:
            index = names.index(descriptor['column'])
            require(all(row[index]['value'] == predicate['value'] for row in public['rows']),
                    'FILTER_RESULT_INVALID')
    return deepcopy(public)


def receipt_projection(public):
    return {k:v for k,v in validate_public(public).items() if k != 'rows'}
