"""Pure bounded Iceberg browsing contract; no transport or model calls.

normalize_request/parse_request validate syntax before discovery. Discovery is
still required to reject a syntactically valid but absent table or column; that
is not a zero-POST claim. select_sql accepts only a complete fresh inventory,
the observed Trino schema and a matching Polaris metadata identity. The caller
must bind those observations to the fixed server and recheck identity after
reading. This module neither authenticates observations nor grants authority.

Public selected rows are explicit user output. receipt_projection omits rows
and SQL. Snapshot/int/decimal values remain exact strings in public JSON.

Example flow (observations come from a separately guarded transport):
  req = request('select', table='smoke.fixture_v1', columns=['id'], limit=25)
  inv = project_inventory(trino(discovery_sql(req)), 'smoke')
  require_discovered(req, inv)
  cols = project_schema(trino(schema_sql(req)))
  before = project_identity(polaris_get(req), req, cols)
  public = project_rows(trino(select_sql(req, inv, cols, before)), req, cols, before)
  verify_identity(before, project_identity(polaris_get(req), req, cols))
  saved = receipt_projection(public)

Scalar support is integer/bigint, boolean, varchar, real/double, decimal(1..38),
and date. Binary, timestamps, UUID-valued columns and nested types are outside
this first reader. Trino JSON numbers must be decoded with parse_float=Decimal;
integers use the JSON integer decoder. Wire-byte/time/terminal-query limits and
the final outer CLI-envelope size are the transport/bridge's responsibility.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import uuid

CATALOG = 'iceberg'
SCHEMAS = ('smoke', 'local_experiment')
MAX_INPUT_BYTES = 4096
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_PUBLIC_BYTES = 256 * 1024
MAX_CELL_BYTES = 4096
MAX_TABLES = 100
MAX_COLUMNS = 32
MAX_SELECTED_COLUMNS = 16
MAX_LIMIT = 100
MAX_SNAPSHOT_ID = 2**63 - 1
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
DECIMAL_TYPE = re.compile(r'decimal\(([1-9][0-9]?), ?(0|[1-9][0-9]?)\)\Z')
SCALAR_TYPES = {'integer', 'bigint', 'boolean', 'varchar', 'real', 'double', 'date'}
IDENTITY_KEYS = {'schema_version', 'catalog', 'schema', 'table', 'table_uuid', 'format_version',
    'schema_id', 'schema_sha256', 'spec_sha256', 'refs_sha256', 'current_snapshot_id',
    'metadata_sha256', 'metadata_location_sha256', 'table_location_sha256', 'columns'}
RESULT_KEYS = {'schema_version', 'table', 'table_uuid', 'schema_sha256', 'snapshot_id', 'columns',
    'rows', 'row_count', 'fetched_row_count', 'truncated', 'requested_limit', 'ordering', 'rows_sha256'}


class ContractError(ValueError):
    """Fixed safe code only; never interpolate caller data into an error."""


def require(condition, code):
    if not condition:
        raise ContractError(code)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                          allow_nan=False).encode('ascii')
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ContractError('JSON_INVALID') from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _bounded(value, maximum, code):
    require(len(canonical(value)) <= maximum, code)
    return value


def _identifier(value):
    require(type(value) is str and IDENTIFIER.fullmatch(value), 'IDENTIFIER_INVALID')
    return value


def _table(value):
    require(type(value) is str and len(value) <= 128 and value.count('.') == 1, 'TABLE_INVALID')
    schema, name = value.split('.')
    require(schema in SCHEMAS, 'SCHEMA_UNSUPPORTED')
    return schema, _identifier(name)


def _snapshot(value, *, nullable=False):
    if nullable and value is None:
        return None
    require(type(value) is int and 0 < value <= MAX_SNAPSHOT_ID, 'SNAPSHOT_INVALID')
    return value


def _public_snapshot(value):
    if value is None:
        return None
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,18}', value), 'SNAPSHOT_INVALID')
    return _snapshot(int(value))


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, 'TABLE_UUID_INVALID')
    except (ValueError, AttributeError):
        raise ContractError('TABLE_UUID_INVALID') from None
    return value


def normalize_request(value):
    require(type(value) is dict, 'REQUEST_INVALID')
    _bounded(value, MAX_INPUT_BYTES, 'INPUT_LIMIT')
    command = value.get('command')
    require(type(command) is str and command in ('tables', 'describe', 'select'), 'COMMAND_INVALID')
    keys = {'tables': {'command', 'schema'}, 'describe': {'command', 'table'},
            'select': {'command', 'table', 'columns', 'limit'}}[command]
    require(set(value) == keys, 'REQUEST_KEYS_INVALID')
    if command == 'tables':
        require(type(value['schema']) is str and value['schema'] in SCHEMAS, 'SCHEMA_UNSUPPORTED')
    else:
        _table(value['table'])
    if command == 'select':
        columns = value['columns']
        require(type(columns) is list and 1 <= len(columns) <= MAX_SELECTED_COLUMNS, 'COLUMNS_INVALID')
        for column in columns:
            _identifier(column)
        require(len(set(columns)) == len(columns), 'DUPLICATE_COLUMNS')
        require(type(value['limit']) is int and 1 <= value['limit'] <= MAX_LIMIT, 'LIMIT_INVALID')
    return deepcopy(value)


def request(command, *, schema=None, table=None, columns=None, limit=None):
    """Convenience constructor; unused arguments must be absent/None."""
    value = {'command': command}
    value.update({k:v for k,v in {'schema':schema, 'table':table, 'columns':columns, 'limit':limit}.items()
                  if v is not None})
    return normalize_request(value)


def parse_request(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_INPUT_BYTES, 'INPUT_LIMIT')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('JSON_INVALID')))
    except (UnicodeError, ValueError, RecursionError):
        raise ContractError('JSON_INVALID') from None
    return normalize_request(value)


def quote(value):
    return '"' + _identifier(value) + '"'


def qualified(table):
    schema, name = _table(table)
    return '.'.join(quote(x) for x in (CATALOG, schema, name))


def discovery_sql(value):
    value = normalize_request(value)
    schema = value['schema'] if value['command'] == 'tables' else _table(value['table'])[0]
    return ('SELECT table_name FROM "iceberg"."information_schema"."tables" '
            f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE' "
            f'ORDER BY table_name LIMIT {MAX_TABLES + 1}')


def schema_sql(value):
    value = normalize_request(value)
    require(value['command'] != 'tables', 'COMMAND_INVALID')
    schema, name = _table(value['table'])
    return ('SELECT column_name, data_type, ordinal_position, is_nullable FROM '
            '"iceberg"."information_schema"."columns" '
            f"WHERE table_schema = '{schema}' AND table_name = '{name}' "
            f'ORDER BY ordinal_position LIMIT {MAX_COLUMNS + 1}')


def _result(value, names, types, max_rows):
    require(type(value) is dict and value.get('columns') == names and value.get('column_types') == types
            and type(value.get('rows')) is list and len(value['rows']) <= max_rows
            and all(type(r) is list and len(r) == len(names) for r in value['rows']), 'RESULT_SCHEMA_INVALID')
    # A transport must also bound actual bytes. This conservative semantic
    # budget adds quotes around JSON decimal numbers without rounding them.
    # The original Decimal values continue to type validation below.
    budget_rows = []
    for row in value['rows']:
        budget_row = []
        for cell in row:
            if type(cell) is Decimal:
                require(cell.is_finite() and len(str(cell)) <= 128, 'CELL_NUMBER_INVALID')
                budget_row.append(str(cell))
            else:
                budget_row.append(cell)
        budget_rows.append(budget_row)
    _bounded(budget_rows, MAX_METADATA_BYTES, 'RESULT_INPUT_LIMIT')
    return value['rows']


def project_inventory(result, schema):
    require(type(schema) is str and schema in SCHEMAS, 'SCHEMA_UNSUPPORTED')
    rows = _result(result, ['table_name'], ['varchar'], MAX_TABLES + 1)
    names = [_identifier(row[0]) for row in rows]
    require(names == sorted(set(names)), 'INVENTORY_INVALID')
    return {'schema_version': 1, 'catalog': CATALOG, 'schema': schema,
            'tables': [schema + '.' + name for name in names[:MAX_TABLES]], 'complete': len(names) <= MAX_TABLES}


def require_discovered(value, inventory):
    value = normalize_request(value)
    require(value['command'] != 'tables', 'COMMAND_INVALID')
    schema, _ = _table(value['table'])
    require(type(inventory) is dict and set(inventory) == {'schema_version','catalog','schema','tables','complete'}
            and type(inventory['schema_version']) is int and inventory['schema_version'] == 1
            and inventory['catalog'] == CATALOG and inventory['schema'] == schema
            and inventory['complete'] is True and type(inventory['tables']) is list
            and len(inventory['tables']) <= MAX_TABLES, 'INVENTORY_INCOMPLETE')
    for table in inventory['tables']:
        require(_table(table)[0] == schema, 'INVENTORY_INVALID')
    require(inventory['tables'] == sorted(set(inventory['tables'])), 'INVENTORY_INVALID')
    require(value['table'] in inventory['tables'], 'TABLE_NOT_DISCOVERED')
    return value


def scalar_type(value):
    require(type(value) is str, 'TYPE_UNSUPPORTED')
    if value in SCALAR_TYPES:
        return value
    decimal = DECIMAL_TYPE.fullmatch(value)
    require(decimal is not None, 'TYPE_UNSUPPORTED')
    precision, scale = map(int, decimal.groups())
    require(1 <= precision <= 38 and 0 <= scale <= precision, 'TYPE_UNSUPPORTED')
    return f'decimal({precision},{scale})'


def _columns(value):
    require(type(value) is list and 1 <= len(value) <= MAX_COLUMNS, 'SCHEMA_COLUMNS_INVALID')
    names = []
    for row in value:
        require(type(row) is dict and set(row) == {'name','type','nullable'}, 'SCHEMA_COLUMNS_INVALID')
        names.append(_identifier(row['name']))
        require(scalar_type(row['type']) == row['type'] and type(row['nullable']) is bool, 'SCHEMA_COLUMNS_INVALID')
    require(len(set(names)) == len(names), 'SCHEMA_COLUMNS_INVALID')
    return deepcopy(value)


def project_schema(result):
    rows = _result(result, ['column_name','data_type','ordinal_position','is_nullable'],
                   ['varchar','varchar','bigint','varchar'], MAX_COLUMNS + 1)
    require(1 <= len(rows) <= MAX_COLUMNS, 'SCHEMA_COLUMN_LIMIT')
    columns = []
    for index, row in enumerate(rows, 1):
        require(type(row[2]) is int and row[2] == index and type(row[3]) is str
                and row[3] in ('YES','NO'), 'SCHEMA_COLUMNS_INVALID')
        columns.append({'name':_identifier(row[0]), 'type':scalar_type(row[1]), 'nullable':row[3] == 'YES'})
    return _columns(columns)


def _location(value):
    require(type(value) is str and len(value) <= 2048
            and re.fullmatch(r's3://shirokuma-lakehouse/[A-Za-z0-9_./=\-]+', value)
            and not any(p in ('', '.', '..') for p in value.split('/')[3:]), 'TABLE_LOCATION_INVALID')
    return value


def project_identity(response, value, columns):
    """Project a GET for exactly request.table; transport must enforce that path.

    REST config/storage credentials are discarded. Existing format-v2 tables
    with scalar current schemas are supported; no table mutation is performed.
    """
    value = normalize_request(value)
    require(value['command'] != 'tables', 'COMMAND_INVALID')
    schema_name, table_name = _table(value['table'])
    columns = _columns(columns)
    _bounded(response, MAX_METADATA_BYTES, 'METADATA_LIMIT')
    require(type(response) is dict and type(response.get('metadata')) is dict, 'METADATA_INVALID')
    metadata = response['metadata']
    identity = _uuid(metadata.get('table-uuid'))
    require(type(metadata.get('format-version')) is int and metadata['format-version'] == 2, 'FORMAT_UNSUPPORTED')
    location = _location(metadata.get('location'))
    metadata_location = _location(response.get('metadata-location'))
    require(metadata_location.startswith(location + '/metadata/') and metadata_location.endswith('.metadata.json'),
            'METADATA_LOCATION_INVALID')
    schema_id = metadata.get('current-schema-id')
    require(type(schema_id) is int and 0 <= schema_id < 2**31, 'SCHEMA_INVALID')
    schemas = metadata.get('schemas')
    require(type(schemas) is list and 1 <= len(schemas) <= 128 and all(type(s) is dict for s in schemas), 'SCHEMA_INVALID')
    active = [s for s in schemas if type(s.get('schema-id')) is int and s['schema-id'] == schema_id]
    require(len(active) == 1 and active[0].get('type') == 'struct', 'SCHEMA_INVALID')
    fields = active[0].get('fields')
    require(type(fields) is list and len(fields) == len(columns), 'SCHEMA_INVALID')
    observed, ids = [], []
    mapping = {'int':'integer','long':'bigint','string':'varchar','float':'real','double':'double',
               'boolean':'boolean','date':'date'}
    for field in fields:
        require(type(field) is dict and type(field.get('id')) is int and 0 < field['id'] < 2**31
                and type(field.get('required')) is bool and type(field.get('type')) is str, 'SCHEMA_INVALID')
        ids.append(field['id'])
        kind = field['type']
        observed.append({'name':_identifier(field.get('name')), 'type':scalar_type(mapping.get(kind, kind)),
                         'nullable':not field['required']})
    require(len(set(ids)) == len(ids) and observed == columns, 'REST_TRINO_SCHEMA_DIFFER')
    specs = metadata.get('partition-specs')
    spec_id = metadata.get('default-spec-id')
    require(type(spec_id) is int and 0 <= spec_id < 2**31 and type(specs) is list and 1 <= len(specs) <= 128
            and all(type(s) is dict and type(s.get('spec-id')) is int and type(s.get('fields')) is list for s in specs)
            and len({s['spec-id'] for s in specs}) == len(specs) and spec_id in {s['spec-id'] for s in specs}, 'SPEC_INVALID')
    snapshots = metadata.get('snapshots', [])
    require(type(snapshots) is list and len(snapshots) <= 1024 and all(type(s) is dict for s in snapshots), 'SNAPSHOT_INVALID')
    snapshot_ids = [_snapshot(s.get('snapshot-id')) for s in snapshots]
    require(len(set(snapshot_ids)) == len(snapshot_ids), 'SNAPSHOT_INVALID')
    current = metadata.get('current-snapshot-id')
    if current is None or type(current) is int and current == -1:
        require(not snapshots, 'SNAPSHOT_INVALID')
        current = None
    else:
        _snapshot(current)
        require(current in snapshot_ids, 'SNAPSHOT_INVALID')
    refs = metadata.get('refs', {})
    require(type(refs) is dict and len(refs) <= 32, 'REFS_INVALID')
    for name, ref in refs.items():
        _identifier(name)
        require(type(ref) is dict and ref.get('type') in ('branch','tag')
                and _snapshot(ref.get('snapshot-id')) in snapshot_ids
                and set(ref) <= {'type','snapshot-id','min-snapshots-to-keep','max-snapshot-age-ms','max-ref-age-ms'}, 'REFS_INVALID')
        for key in set(ref) - {'type','snapshot-id'}:
            require(type(ref[key]) is int and 0 < ref[key] < 2**63, 'REFS_INVALID')
    require((current is None and not refs) or refs.get('main') == {'type':'branch','snapshot-id':current}
            or current is not None and type(refs.get('main')) is dict
            and refs['main'].get('type') == 'branch' and refs['main'].get('snapshot-id') == current, 'REFS_INVALID')
    return {'schema_version':1, 'catalog':CATALOG, 'schema':schema_name, 'table':table_name,
            'table_uuid':identity, 'format_version':2, 'schema_id':schema_id,
            'schema_sha256':digest(active[0]), 'spec_sha256':digest({'specs':specs,'default':spec_id}),
            'refs_sha256':digest(refs), 'current_snapshot_id':str(current) if current is not None else None,
            'metadata_sha256':digest(metadata), 'metadata_location_sha256':digest(metadata_location),
            'table_location_sha256':digest(location), 'columns':columns}


def validate_identity(identity, value, columns):
    value = normalize_request(value)
    require(value['command'] != 'tables', 'COMMAND_INVALID')
    schema, table = _table(value['table'])
    require(type(identity) is dict and set(identity) == IDENTITY_KEYS
            and type(identity['schema_version']) is int and identity['schema_version'] == 1
            and identity['catalog'] == CATALOG and identity['schema'] == schema and identity['table'] == table
            and type(identity['format_version']) is int and identity['format_version'] == 2
            and type(identity['schema_id']) is int and 0 <= identity['schema_id'] < 2**31, 'IDENTITY_INVALID')
    _uuid(identity['table_uuid'])
    _public_snapshot(identity['current_snapshot_id'])
    for key in ('schema_sha256','spec_sha256','refs_sha256','metadata_sha256','metadata_location_sha256','table_location_sha256'):
        require(type(identity[key]) is str and SHA256.fullmatch(identity[key]), 'IDENTITY_INVALID')
    require(_columns(identity['columns']) == _columns(columns), 'IDENTITY_SCHEMA_DIFFER')
    return deepcopy(identity)


def verify_identity(before, after):
    """Reject invalid projections and any drift; never promote an uncertain read."""
    require(type(before) is dict and type(after) is dict and set(before) == IDENTITY_KEYS
            and set(after) == IDENTITY_KEYS and type(before['schema']) is str
            and type(before['table']) is str, 'IDENTITY_CHANGED')
    value = request('describe', table=before['schema'] + '.' + before['table'])
    validate_identity(before, value, before['columns'])
    validate_identity(after, value, before['columns'])
    require(canonical(before) == canonical(after), 'IDENTITY_CHANGED')
    return True


def selected_columns(value, columns):
    value = normalize_request(value)
    require(value['command'] == 'select', 'COMMAND_INVALID')
    known = {c['name']:c for c in _columns(columns)}
    require(all(name in known for name in value['columns']), 'COLUMN_NOT_DISCOVERED')
    return [deepcopy(known[name]) for name in value['columns']]


def select_sql(value, inventory, columns, identity):
    value = require_discovered(value, inventory)
    selected = selected_columns(value, columns)
    identity = validate_identity(identity, value, columns)
    snapshot = _public_snapshot(identity['current_snapshot_id'])
    require(snapshot is not None, 'NO_CURRENT_SNAPSHOT')
    return ('SELECT ' + ', '.join(quote(c['name']) for c in selected) + ' FROM ' + qualified(value['table'])
            + f' FOR VERSION AS OF {snapshot} LIMIT {value["limit"] + 1}')


def _cell(value, spec):
    kind = spec['type']
    if value is None:
        require(spec['nullable'], 'CELL_NULL_INVALID')
        return {'type':kind, 'value':None}
    if kind in ('integer','bigint'):
        bits = 32 if kind == 'integer' else 64
        require(type(value) is int and -(2**(bits-1)) <= value < 2**(bits-1), 'CELL_INTEGER_INVALID')
        result = str(value)
    elif kind == 'boolean':
        require(type(value) is bool, 'CELL_BOOLEAN_INVALID')
        result = value
    elif kind == 'varchar':
        require(type(value) is str, 'CELL_STRING_INVALID')
        try:
            require(len(value.encode('utf-8')) <= MAX_CELL_BYTES, 'CELL_LIMIT')
        except UnicodeError:
            raise ContractError('CELL_STRING_INVALID') from None
        result = value
    elif kind == 'date':
        require(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value), 'CELL_DATE_INVALID')
        try:
            require(date.fromisoformat(value).isoformat() == value, 'CELL_DATE_INVALID')
        except ValueError:
            raise ContractError('CELL_DATE_INVALID') from None
        result = value
    elif kind in ('real','double'):
        require(type(value) in (int,float,Decimal), 'CELL_FLOAT_INVALID')
        if type(value) is Decimal:
            require(value.is_finite() and len(str(value)) <= 128, 'CELL_FLOAT_INVALID')
        try:
            number = float(value)
        except OverflowError:
            raise ContractError('CELL_FLOAT_INVALID') from None
        require(math.isfinite(number) and (kind != 'real' or abs(number) <= 3.4028234663852886e38), 'CELL_FLOAT_INVALID')
        result = repr(number)
    else:
        match = DECIMAL_TYPE.fullmatch(kind)
        require(match is not None and type(value) in (str,Decimal), 'CELL_DECIMAL_INVALID')
        precision, scale = map(int, match.groups())
        require(len(str(value)) <= 128, 'CELL_DECIMAL_INVALID')
        if type(value) is str:
            require(re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value), 'CELL_DECIMAL_INVALID')
        try:
            number = Decimal(value)
            require(number.is_finite(), 'CELL_DECIMAL_INVALID')
            sign, digits, exponent = number.as_tuple()
            coefficient = int(''.join(map(str,digits)))
            shift = exponent + scale
            if not coefficient:
                units = 0
            elif shift >= 0:
                require(len(digits) + shift <= precision, 'CELL_DECIMAL_INVALID')
                units = coefficient * 10**shift
            else:
                require(-shift <= len(digits), 'CELL_DECIMAL_INVALID')
                divisor = 10**(-shift)
                require(coefficient % divisor == 0, 'CELL_DECIMAL_INVALID')
                units = coefficient // divisor
            require(units < 10**precision, 'CELL_DECIMAL_INVALID')
            text = str(units).zfill(scale+1)
            result = ('-' if sign and units else '') + (text[:-scale]+'.'+text[-scale:] if scale else text)
        except (InvalidOperation, ValueError, OverflowError):
            raise ContractError('CELL_DECIMAL_INVALID') from None
    return {'type':kind, 'value':result}


def project_rows(result, value, columns, identity):
    value = normalize_request(value)
    selected = selected_columns(value, columns)
    identity = validate_identity(identity, value, columns)
    require(identity['current_snapshot_id'] is not None, 'NO_CURRENT_SNAPSHOT')
    require(type(result) is dict and type(result.get('column_types')) is list, 'RESULT_SCHEMA_INVALID')
    normalized = dict(result, column_types=[scalar_type(t) for t in result['column_types']])
    raw = _result(normalized, [c['name'] for c in selected], [c['type'] for c in selected], value['limit'] + 1)
    all_rows = [[_cell(cell, spec) for cell, spec in zip(row, selected)] for row in raw]
    observed = all_rows[:value['limit']]
    public = {'schema_version':1, 'table':CATALOG + '.' + value['table'], 'table_uuid':identity['table_uuid'],
              'schema_sha256':identity['schema_sha256'], 'snapshot_id':identity['current_snapshot_id'],
              'columns':selected, 'rows':observed, 'row_count':len(observed), 'fetched_row_count':len(all_rows),
              'truncated':len(all_rows) > value['limit'], 'requested_limit':value['limit'],
              'ordering':'unspecified', 'rows_sha256':digest(observed)}
    return deepcopy(_bounded(public, MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT'))


def receipt_projection(public):
    """Only the canonical project_rows result is accepted; raw rows are omitted."""
    require(type(public) is dict and set(public) == RESULT_KEYS, 'PUBLIC_RESULT_INVALID')
    _bounded(public, MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
    require(type(public['schema_version']) is int and public['schema_version'] == 1
            and type(public['rows']) is list and type(public['row_count']) is int
            and type(public['requested_limit']) is int and 1 <= public['requested_limit'] <= MAX_LIMIT
            and len(public['rows']) == public['row_count'] <= public['requested_limit']
            and type(public['fetched_row_count']) is int
            and public['fetched_row_count'] in (public['row_count'], public['requested_limit'] + 1)
            and public['row_count'] == min(public['fetched_row_count'], public['requested_limit'])
            and type(public['truncated']) is bool
            and public['truncated'] == (public['fetched_row_count'] > public['requested_limit'])
            and public['ordering'] == 'unspecified' and public['rows_sha256'] == digest(public['rows']), 'PUBLIC_RESULT_INVALID')
    _uuid(public['table_uuid'])
    require(type(public['table']) is str and public['table'].startswith(CATALOG + '.'), 'PUBLIC_RESULT_INVALID')
    _table(public['table'][len(CATALOG)+1:])
    require(type(public['schema_sha256']) is str and SHA256.fullmatch(public['schema_sha256'])
            and _public_snapshot(public['snapshot_id']) is not None, 'PUBLIC_RESULT_INVALID')
    selected = _columns(public['columns'])
    require(len(selected) <= MAX_SELECTED_COLUMNS, 'PUBLIC_RESULT_INVALID')
    for row in public['rows']:
        require(type(row) is list and len(row) == len(selected), 'PUBLIC_RESULT_INVALID')
        for cell, spec in zip(row, selected):
            require(type(cell) is dict and set(cell) == {'type','value'} and cell['type'] == spec['type'], 'PUBLIC_RESULT_INVALID')
            raw = cell['value']
            if raw is not None and spec['type'] in ('integer','bigint'):
                require(type(raw) is str and len(raw) <= 20
                        and re.fullmatch(r'-?(?:0|[1-9][0-9]*)', raw), 'PUBLIC_RESULT_INVALID')
                raw = int(raw)
            elif raw is not None and spec['type'] in ('real','double'):
                require(type(raw) is str and len(raw) <= 32, 'PUBLIC_RESULT_INVALID')
                try:
                    raw = float(raw)
                except ValueError:
                    raise ContractError('PUBLIC_RESULT_INVALID') from None
            require(_cell(raw, spec) == cell, 'PUBLIC_RESULT_INVALID')
    return deepcopy({k:v for k,v in public.items() if k != 'rows'})
