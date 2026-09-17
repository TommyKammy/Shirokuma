"""Pure retained-snapshot browsing, separate from the current-head Reader v1.

The caller authenticates two fresh metadata windows for the exact discovered
table. This module validates metadata and compiles only a numeric snapshot
SELECT; it does not authenticate a saved projection or perform any I/O. An ID
missing from metadata is not-retained, not proof that it once existed/expired.
Missing parents are legitimate after expiration; retained cycles are invalid.

API: normalize_request/parse_request -> request; base_request -> Reader v1
transport request; project_window(response, request, columns) -> {schema_version,
identity, snapshots}; select_sql(request, inventory, columns, window) -> SQL;
project_rows(result, request, columns, window) or project_snapshots(window,
request) -> explicit public output. verify_window(before, after) must succeed
and owned queries/tunnels must close before publishing either output.

identity always describes the actual current head. selected_snapshot is a
separate record, even when reading the head explicitly. Public schema_sha256
describes the observed current schema; compatibility requires identical ordered
field IDs/names/scalar types/required flags in the selected snapshot's schema.
No historical schema is inferred when schema-id is absent or no longer retained.
Receipt projections omit rows and SQL. Existing Reader/diagnose v1 is unchanged.
"""
from copy import deepcopy
import json
import re

from tools.local_experiment.query_read import contract as R

ContractError = R.ContractError
require, canonical, digest = R.require, R.canonical, R.digest
CATALOG, SCHEMAS = R.CATALOG, R.SCHEMAS
MAX_INPUT_BYTES, MAX_METADATA_BYTES, MAX_PUBLIC_BYTES = R.MAX_INPUT_BYTES, R.MAX_METADATA_BYTES, R.MAX_PUBLIC_BYTES
MAX_SNAPSHOTS = 64
MAX_TIMESTAMP_MS = 253402300799999  # Last millisecond in year 9999; exact in JS.
OPERATIONS = ('append', 'replace', 'overwrite', 'delete')
COMPATIBILITY = ('compatible', 'schema-missing', 'schema-not-retained', 'schema-mismatch', 'schema-unsupported')
RECORD_KEYS = {'snapshot_id', 'parent_snapshot_id', 'sequence_number', 'timestamp_ms',
               'schema_id', 'operation', 'is_current', 'parent_retained', 'schema_compatibility'}
WINDOW_KEYS = {'schema_version', 'identity', 'snapshots'}
SNAPSHOTS_KEYS = {'schema_version', 'table', 'table_uuid', 'current_snapshot_id', 'snapshots', 'complete', 'ordering'}
RESULT_KEYS = R.RESULT_KEYS | {'current_snapshot_id', 'selected_snapshot'}
ERROR_CODES = frozenset({'SNAPSHOT_LIMIT', 'SNAPSHOT_HISTORY_INVALID', 'SNAPSHOT_SCHEMA_INVALID',
    'SNAPSHOT_NOT_RETAINED', 'SNAPSHOT_SCHEMA_UNSUPPORTED', 'SNAPSHOT_WINDOW_INVALID', 'SNAPSHOT_WINDOW_CHANGED'})


def snapshot_id(value):
    """Canonical, positive signed-int64 text; no float/bool conversion."""
    require(R._public_snapshot(value) is not None, 'SNAPSHOT_INVALID')
    return value


def normalize_request(value):
    require(type(value) is dict, 'REQUEST_INVALID')
    R._bounded(value, MAX_INPUT_BYTES, 'INPUT_LIMIT')
    command = value.get('command')
    require(type(command) is str and command in ('snapshots', 'select'), 'COMMAND_INVALID')
    keys = {'command', 'table'} if command == 'snapshots' else {'command', 'table', 'columns', 'limit', 'snapshot_id'}
    require(set(value) == keys, 'REQUEST_KEYS_INVALID')
    base = {k:v for k,v in value.items() if k != 'snapshot_id'}
    if command == 'snapshots':
        base['command'] = 'describe'
    R.normalize_request(base)
    if command == 'select':
        snapshot_id(value['snapshot_id'])
    return deepcopy(value)


def request(command, *, table, columns=None, limit=None, snapshot_id=None):
    value = {'command':command, 'table':table}
    value.update({k:v for k,v in {'columns':columns, 'limit':limit, 'snapshot_id':snapshot_id}.items() if v is not None})
    return normalize_request(value)


def parse_request(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_INPUT_BYTES, 'INPUT_LIMIT')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('JSON_INVALID')))
    except (UnicodeError, ValueError, RecursionError):
        raise ContractError('JSON_INVALID') from None
    return normalize_request(value)


def base_request(value):
    value = normalize_request(value)
    return R.normalize_request({k:('describe' if k == 'command' and v == 'snapshots' else v)
                                for k,v in value.items() if k != 'snapshot_id'})


def discovery_sql(value):
    return R.discovery_sql(base_request(value))


def schema_sql(value):
    return R.schema_sql(base_request(value))


def require_discovered(value, inventory):
    value = normalize_request(value)
    R.require_discovered(base_request(value), inventory)
    return value


def selected_columns(value, columns):
    return R.selected_columns(base_request(value), columns)


def _schema_fields(schema):
    """None marks a well-framed but unsupported historical scalar schema."""
    require(schema.get('type') == 'struct' and type(schema.get('fields')) is list,
            'SNAPSHOT_SCHEMA_INVALID')
    fields = schema['fields']
    require(len(fields) <= 1024, 'SNAPSHOT_SCHEMA_INVALID')
    normalized, names, ids, supported = [], [], [], 1 <= len(fields) <= R.MAX_COLUMNS
    mapping = {'int':'integer', 'long':'bigint', 'string':'varchar', 'float':'real',
               'double':'double', 'boolean':'boolean', 'date':'date'}
    for field in fields:
        require(type(field) is dict and type(field.get('id')) is int and 0 < field['id'] < 2**31
                and type(field.get('name')) is str and type(field.get('required')) is bool
                and type(field.get('type')) in (str, dict), 'SNAPSHOT_SCHEMA_INVALID')
        names.append(field['name']); ids.append(field['id'])
        try:
            name = R._identifier(field['name'])
            kind = R.scalar_type(mapping.get(field['type'], field['type'])) if type(field['type']) is str else None
            supported = supported and kind is not None
        except ContractError:
            name, kind, supported = field['name'], None, False
        normalized.append({'id':field['id'], 'name':name, 'type':kind, 'required':field['required']})
    require(len(ids) == len(set(ids)) and len(names) == len(set(names)), 'SNAPSHOT_SCHEMA_INVALID')
    return normalized if supported else None


def _order(row):
    # ID is only a deterministic tie-breaker, never an ancestry comparison.
    return row['timestamp_ms'], int(row['sequence_number']), int(row['snapshot_id'])


def _record(value):
    require(type(value) is dict and set(value) == RECORD_KEYS, 'SNAPSHOT_HISTORY_INVALID')
    snapshot_id(value['snapshot_id'])
    if value['parent_snapshot_id'] is not None:
        snapshot_id(value['parent_snapshot_id'])
        require(value['parent_snapshot_id'] != value['snapshot_id'], 'SNAPSHOT_HISTORY_INVALID')
    seq = value['sequence_number']
    require(type(seq) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,18})', seq)
            and int(seq) <= R.MAX_SNAPSHOT_ID, 'SNAPSHOT_HISTORY_INVALID')
    require(type(value['timestamp_ms']) is int and 0 <= value['timestamp_ms'] <= MAX_TIMESTAMP_MS
            and (value['schema_id'] is None or type(value['schema_id']) is int and 0 <= value['schema_id'] < 2**31)
            and type(value['operation']) is str and value['operation'] in OPERATIONS
            and type(value['is_current']) is bool and type(value['parent_retained']) is bool
            and type(value['schema_compatibility']) is str and value['schema_compatibility'] in COMPATIBILITY,
            'SNAPSHOT_HISTORY_INVALID')
    require((value['schema_id'] is None) == (value['schema_compatibility'] == 'schema-missing')
            and (value['parent_snapshot_id'] is not None or value['parent_retained'] is False), 'SNAPSHOT_HISTORY_INVALID')
    return deepcopy(value)


def _records(rows, current):
    require(type(rows) is list and len(rows) <= MAX_SNAPSHOTS, 'SNAPSHOT_LIMIT')
    rows = [_record(row) for row in rows]
    ids = {row['snapshot_id'] for row in rows}
    require(len(ids) == len(rows) and rows == sorted(rows, key=_order), 'SNAPSHOT_HISTORY_INVALID')
    require((not rows and current is None) or current in ids, 'SNAPSHOT_HISTORY_INVALID')
    parents = {row['snapshot_id']:row['parent_snapshot_id'] for row in rows}
    for row in rows:
        require(row['is_current'] == (row['snapshot_id'] == current)
                and row['parent_retained'] == (row['parent_snapshot_id'] in ids), 'SNAPSHOT_HISTORY_INVALID')
        seen, cursor = set(), row['snapshot_id']
        while cursor in parents:
            require(cursor not in seen, 'SNAPSHOT_HISTORY_INVALID')
            seen.add(cursor); cursor = parents[cursor]
    return rows


def project_window(response, value, columns):
    """Project a fixed, authenticated table GET, with no config/credential copy."""
    value = normalize_request(value)
    identity = R.project_identity(response, base_request(value), columns)
    metadata = response['metadata']
    snapshots = metadata.get('snapshots', [])
    require(len(snapshots) <= MAX_SNAPSHOTS, 'SNAPSHOT_LIMIT')
    schemas = {}
    for schema in metadata['schemas']:
        sid = schema.get('schema-id')
        require(type(sid) is int and 0 <= sid < 2**31 and sid not in schemas, 'SNAPSHOT_SCHEMA_INVALID')
        schemas[sid] = _schema_fields(schema)
    current_fields = schemas[identity['schema_id']]
    require(current_fields is not None, 'SNAPSHOT_SCHEMA_INVALID')
    ids = {snapshot['snapshot-id'] for snapshot in snapshots}
    projected = []
    for snapshot in snapshots:
        parent = snapshot.get('parent-snapshot-id')
        if parent is not None:
            R._snapshot(parent)
        sid = snapshot.get('schema-id')
        require(sid is None or type(sid) is int and 0 <= sid < 2**31, 'SNAPSHOT_SCHEMA_INVALID')
        if sid is None:
            compatibility = 'schema-missing'
        elif sid not in schemas:
            compatibility = 'schema-not-retained'
        elif schemas[sid] is None:
            compatibility = 'schema-unsupported'
        else:
            compatibility = 'compatible' if schemas[sid] == current_fields else 'schema-mismatch'
        sequence = snapshot.get('sequence-number')
        require(type(sequence) is int and 0 <= sequence <= R.MAX_SNAPSHOT_ID, 'SNAPSHOT_HISTORY_INVALID')
        require(type(snapshot.get('summary')) is dict, 'SNAPSHOT_HISTORY_INVALID')
        projected.append({'snapshot_id':str(snapshot['snapshot-id']), 'parent_snapshot_id':str(parent) if parent is not None else None,
            'sequence_number':str(sequence), 'timestamp_ms':snapshot.get('timestamp-ms'), 'schema_id':sid,
            'operation':snapshot['summary'].get('operation'), 'is_current':str(snapshot['snapshot-id']) == identity['current_snapshot_id'],
            'parent_retained':parent in ids, 'schema_compatibility':compatibility})
    # Validate before sorting to keep malformed values away from comparisons.
    projected = sorted((_record(row) for row in projected), key=_order)
    window = {'schema_version':2, 'identity':identity, 'snapshots':projected}
    return validate_window(window, value, columns)


def validate_window(window, value, columns=None):
    value = normalize_request(value)
    require(type(window) is dict and set(window) == WINDOW_KEYS and type(window['schema_version']) is int
            and window['schema_version'] == 2 and type(window['identity']) is dict, 'SNAPSHOT_WINDOW_INVALID')
    R._bounded(window, MAX_METADATA_BYTES, 'METADATA_LIMIT')
    identity = window['identity']
    R.validate_identity(identity, base_request(value), identity.get('columns') if columns is None else columns)
    _records(window['snapshots'], identity['current_snapshot_id'])
    return deepcopy(window)


def verify_window(before, after):
    require(type(before) is dict and type(before.get('identity')) is dict, 'SNAPSHOT_WINDOW_INVALID')
    identity = before['identity']
    require(type(identity.get('schema')) is str and type(identity.get('table')) is str, 'SNAPSHOT_WINDOW_INVALID')
    value = request('snapshots', table=identity['schema'] + '.' + identity['table'])
    validate_window(before, value); validate_window(after, value)
    require(canonical(before) == canonical(after), 'SNAPSHOT_WINDOW_CHANGED')
    return True


def select_snapshot(window, value):
    value = normalize_request(value)
    require(value['command'] == 'select', 'COMMAND_INVALID')
    window = validate_window(window, value)
    matches = [row for row in window['snapshots'] if row['snapshot_id'] == value['snapshot_id']]
    require(len(matches) == 1, 'SNAPSHOT_NOT_RETAINED')
    require(matches[0]['schema_compatibility'] == 'compatible', 'SNAPSHOT_SCHEMA_UNSUPPORTED')
    return deepcopy(matches[0])


def select_sql(value, inventory, columns, window):
    value = require_discovered(value, inventory)
    selected = selected_columns(value, columns)
    validate_window(window, value, columns)
    snapshot = select_snapshot(window, value)
    # Trino 483 types small bare integer literals as INTEGER, while Iceberg's
    # numeric version pointer accepts BIGINT. Type every canonical ID explicitly.
    return ('SELECT ' + ', '.join(R.quote(c['name']) for c in selected) + ' FROM ' + R.qualified(value['table'])
            + f' FOR VERSION AS OF BIGINT \'{snapshot["snapshot_id"]}\' LIMIT {value["limit"] + 1}')


def project_snapshots(window, value):
    value = normalize_request(value)
    require(value['command'] == 'snapshots', 'COMMAND_INVALID')
    window = validate_window(window, value)
    identity = window['identity']
    public = {'schema_version':2, 'table':CATALOG + '.' + value['table'], 'table_uuid':identity['table_uuid'],
        'current_snapshot_id':identity['current_snapshot_id'], 'snapshots':window['snapshots'],
        'complete':True, 'ordering':'timestamp-asc'}
    return validate_public(public, value)


def project_rows(result, value, columns, window):
    value = normalize_request(value)
    selected = selected_columns(value, columns)
    window = validate_window(window, value, columns)
    snapshot = select_snapshot(window, value)
    identity = window['identity']
    require(type(result) is dict and type(result.get('column_types')) is list, 'RESULT_SCHEMA_INVALID')
    normalized = dict(result, column_types=[R.scalar_type(t) for t in result['column_types']])
    raw = R._result(normalized, [c['name'] for c in selected], [c['type'] for c in selected], value['limit'] + 1)
    all_rows = [[R._cell(cell, spec) for cell, spec in zip(row, selected)] for row in raw]
    observed = all_rows[:value['limit']]
    public = {'schema_version':2, 'table':CATALOG + '.' + value['table'], 'table_uuid':identity['table_uuid'],
        'schema_sha256':identity['schema_sha256'], 'snapshot_id':snapshot['snapshot_id'], 'selected_snapshot':snapshot,
        'current_snapshot_id':identity['current_snapshot_id'], 'columns':selected, 'rows':observed,
        'row_count':len(observed), 'fetched_row_count':len(all_rows), 'truncated':len(all_rows) > value['limit'],
        'requested_limit':value['limit'], 'ordering':'unspecified', 'rows_sha256':digest(observed)}
    return validate_public(public, value)


def validate_public(public, value=None):
    require(type(public) is dict and type(public.get('schema_version')) is int
            and public['schema_version'] == 2, 'PUBLIC_RESULT_INVALID')
    R._bounded(public, MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
    require(type(public.get('table')) is str and public['table'].startswith(CATALOG + '.'), 'PUBLIC_RESULT_INVALID')
    table = public['table'][len(CATALOG)+1:]
    R._table(table); R._uuid(public.get('table_uuid'))
    current = public.get('current_snapshot_id')
    R._public_snapshot(current)
    if set(public) == SNAPSHOTS_KEYS:
        require(public['complete'] is True and public['ordering'] == 'timestamp-asc', 'PUBLIC_RESULT_INVALID')
        _records(public['snapshots'], current)
        expected = request('snapshots', table=table)
    else:
        require(set(public) == RESULT_KEYS and current is not None, 'PUBLIC_RESULT_INVALID')
        record = _record(public['selected_snapshot'])
        require(record['snapshot_id'] == public['snapshot_id'] and record['schema_compatibility'] == 'compatible'
                and record['is_current'] == (record['snapshot_id'] == current), 'PUBLIC_RESULT_INVALID')
        # Reuse scalar/row-budget validation only. No identity/current head is rewritten.
        legacy_rows = {k:v for k,v in public.items() if k in R.RESULT_KEYS}
        legacy_rows['schema_version'] = 1
        R.receipt_projection(legacy_rows)
        expected = request('select', table=table, columns=[c['name'] for c in public['columns']],
                           limit=public['requested_limit'], snapshot_id=public['snapshot_id'])
    if value is not None:
        require(normalize_request(value) == expected, 'PUBLIC_RESULT_INVALID')
    return deepcopy(public)


def receipt_projection(public):
    """Validate explicit output; persist no SQL, rows, raw metadata or credentials."""
    public = validate_public(public)
    return {k:v for k,v in public.items() if k != 'rows'}
