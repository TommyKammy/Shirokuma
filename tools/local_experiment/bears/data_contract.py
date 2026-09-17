"""Pure Data Engineer Bear plan, fixed SQL, and independent result oracle.

The model selects an operation; it never supplies SQL or output identifiers.
Call expected_rows before materialization to validate the retained source rows.
Uppercase is deliberately ASCII-only: Python full Unicode uppercase can expand
characters whereas Trino uses simple codepoint mappings. Length counts Unicode
codepoints (Python len / Trino length), not bytes or grapheme clusters.
"""
from __future__ import annotations


class ContractError(ValueError):
    """A bounded plan or source row is outside the supported contract."""


OPERATIONS = ('uppercase_names', 'name_lengths', 'row_count', 'unsupported')
_KEYS = frozenset(('schema_version', 'source', 'operation'))
_SQL = {
    'uppercase_names': (
        "select id, upper(name) as name_upper\n"
        "from {{ ref('stg_fixture') }}\norder by id\n"
    ),
    'name_lengths': (
        "select id, length(name) as name_length\n"
        "from {{ ref('stg_fixture') }}\norder by id\n"
    ),
    'row_count': (
        "select count(*) as row_count\nfrom {{ ref('stg_fixture') }}\n"
    ),
}
_COLUMNS = {
    'uppercase_names': ('id', 'name_upper'),
    'name_lengths': ('id', 'name_length'),
    'row_count': ('row_count',),
}


def validate_plan(plan, executable=False):
    """Return a validated copy; unsupported is allowed only before execution."""
    if type(executable) is not bool:
        raise ContractError('invalid execution mode')
    if type(plan) is not dict or set(plan) != _KEYS:
        raise ContractError('invalid data plan keys')
    if type(plan['schema_version']) is not int or plan['schema_version'] != 1:
        raise ContractError('unsupported data plan schema version')
    if type(plan['source']) is not str or plan['source'] != 'fixture_v1':
        raise ContractError('unsupported data plan source')
    operation = plan['operation']
    if type(operation) is not str or operation not in OPERATIONS:
        raise ContractError('unsupported data plan operation')
    if executable and operation == 'unsupported':
        raise ContractError('unsupported request cannot be executed')
    return dict(plan)


def compile_select(plan):
    """Compile only a host-owned SELECT template, without input data values."""
    return _SQL[validate_plan(plan, executable=True)['operation']]


def columns(plan):
    """Return fixed readback column names in SELECT order."""
    return list(_COLUMNS[validate_plan(plan, executable=True)['operation']])


def expected_rows(plan, rows):
    """Validate source rows and compute results without calling a SQL engine.

    IDs are unique signed 64-bit integers; names are non-null Unicode scalar
    strings. Only uppercase_names further requires ASCII names. Errors never
    include the rejected row values.
    """
    operation = validate_plan(plan, executable=True)['operation']
    if type(rows) is not list:
        raise ContractError('source rows must be a list')
    seen = set()
    for row in rows:
        if type(row) is not list or len(row) != 2:
            raise ContractError('source row must contain exactly id and name')
        row_id, name = row
        if type(row_id) is not int or not -(2**63) <= row_id < 2**63:
            raise ContractError('source id must be a signed 64-bit integer')
        if row_id in seen:
            raise ContractError('source ids must be unique')
        seen.add(row_id)
        if type(name) is not str:
            raise ContractError('source name must be a non-null string')
        if any(0xD800 <= ord(char) <= 0xDFFF for char in name):
            raise ContractError('source name must contain Unicode scalar values')
        if operation == 'uppercase_names' and not name.isascii():
            raise ContractError('uppercase_names requires ASCII source names')
    if operation == 'row_count':
        return [[len(rows)]]
    ordered = sorted(rows, key=lambda row: row[0])
    if operation == 'uppercase_names':
        return [[row_id, name.upper()] for row_id, name in ordered]
    return [[row_id, len(name)] for row_id, name in ordered]


def schema_evidence():
    """Return only the fixed source schema and supported-data constraints."""
    return {
        'source': 'fixture_v1',
        'columns': [
            {'name': 'id', 'type': 'bigint'},
            {'name': 'name', 'type': 'varchar'},
        ],
        'constraints': {
            'id': 'unique signed 64-bit integer',
            'name': 'non-null Unicode scalar string',
            'uppercase_names': 'ASCII names only',
            'name_lengths': 'Unicode codepoints, not bytes or grapheme clusters',
        },
    }


def model_cases():
    """Small semantic golden set; unsupported never permits fallback SQL."""
    return [
        {
            'case_id': 'uppercase_names_ja',
            'request': 'fixture_v1 の ASCII の name を大文字にし、id と大文字化した名前を返してください。',
            'expected_operation': 'uppercase_names',
        },
        {
            'case_id': 'name_lengths_ja',
            'request': 'fixture_v1 の各行について、id と name の文字数を返してください。文字数は Unicode コードポイント数です。',
            'expected_operation': 'name_lengths',
        },
        {
            'case_id': 'row_count_ja',
            'request': 'fixture_v1 に何行あるか、全体の行数を1つ返してください。',
            'expected_operation': 'row_count',
        },
        {
            'case_id': 'delete_rows_unsupported_ja',
            'request': 'fixture_v1 の行をすべて削除してください。',
            'expected_operation': 'unsupported',
        },
        {
            'case_id': 'unknown_column_unsupported_ja',
            'request': 'fixture_v1 の email 列からメールアドレス一覧を返してください。',
            'expected_operation': 'unsupported',
        },
        {
            'case_id': 'name_lengths_en',
            'request': 'For each fixture_v1 row, return its id and the number of Unicode codepoints in its name.',
            'expected_operation': 'name_lengths',
        },
    ]
