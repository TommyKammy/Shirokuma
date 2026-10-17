"""Ingest local CSV, JSONL, and Parquet files into the resident Iceberg lake."""
import argparse
import csv
from datetime import date
import json
import math
from pathlib import Path
import re
import sys
import time

import bears_resident as bears
import warehouse_resident as wr

SCHEMAS = {'raw', 'sandbox', 'staging', 'marts'}
FORMATS = {'.csv': 'csv', '.tsv': 'csv', '.jsonl': 'jsonl',
           '.ndjson': 'jsonl', '.json': 'jsonl', '.parquet': 'parquet'}


def sanitize(value):
    name = re.sub(r'[^a-z0-9_]', '_', str(value).lower()) or 'col'
    return 'col_' + name if name[0].isdigit() else name


def target_table(value, path):
    parts = (value or sanitize(path.stem)).split('.')
    if len(parts) == 1:
        parts = ['iceberg', 'raw'] + parts
    elif len(parts) == 2:
        parts = ['iceberg'] + parts
    if (len(parts) != 3 or parts[0] != 'iceberg' or parts[1] not in SCHEMAS
            or not all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', p) for p in parts)):
        raise ValueError('table must be [iceberg.][raw|sandbox|staging|marts.]<identifier>')
    return '.'.join(parts)


def read_file(path, format='auto'):
    detected = FORMATS.get(path.suffix.lower()) if format == 'auto' else format
    if detected not in {'csv', 'jsonl', 'parquet'}:
        raise ValueError('cannot detect file format; use --format')
    if detected == 'parquet':
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise ValueError('parquet ingestion requires pyarrow') from exc
        table = parquet.read_table(path)
        columns, rows = table.column_names, table.to_pylist()
    else:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            if detected == 'csv':
                reader = csv.DictReader(stream, delimiter='\t' if path.suffix.lower() == '.tsv' else ',')
                columns = reader.fieldnames or []
                # Positional keys preserve distinct values under duplicate headers.
                reader.fieldnames = list(range(len(columns)))
                rows = list(reader)
                if any(None in row for row in rows):
                    raise ValueError('CSV row has more values than headers')
            else:
                content = stream.read()
                rows = (json.loads(content) if content.lstrip().startswith('[') else
                        [json.loads(line) for line in content.splitlines() if line.strip()])
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    raise ValueError('JSON input must contain objects')
                columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns:
        raise ValueError('no columns found')
    names, used = [], set()
    for column in columns:
        base = sanitize(column)
        name, suffix = base, 2
        while name in used:
            name, suffix = f'{base}_{suffix}', suffix + 1
        used.add(name)
        names.append(name)
    keys = range(len(columns)) if detected == 'csv' else columns
    values = [[row.get(column) for column in keys] for row in rows]
    schema = [(name, infer_type([row[i] for row in values])) for i, name in enumerate(names)]
    return detected, schema, values


def is_null(value):
    return value is None or isinstance(value, str) and value == ''


def integer(value):
    return (not isinstance(value, bool) and
            (isinstance(value, int) or isinstance(value, str) and
             re.fullmatch(r'[+-]?[0-9]+', value.strip()) is not None) and
            -(2**63) <= int(value) < 2**63)


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        return math.isfinite(float(value))
    except (ValueError, OverflowError):
        return False


def is_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def infer_type(values):
    values = [v for v in values if not is_null(v)]
    if not values:
        return 'VARCHAR'
    if all(isinstance(v, bool) or isinstance(v, str) and v.lower() in {'true', 'false'} for v in values):
        return 'BOOLEAN'
    if all(integer(v) for v in values):
        return 'BIGINT'
    if all(numeric(v) for v in values):
        return 'DOUBLE'
    if all(is_date(v) for v in values):
        return 'DATE'
    return 'VARCHAR'


def literal(value, sql_type):
    if is_null(value):
        return f'CAST(NULL AS {sql_type})'
    if sql_type == 'BOOLEAN':
        return 'TRUE' if str(value).lower() == 'true' else 'FALSE'
    # Explicit casts keep CTAS types stable even when the first batch contains
    # only small integers or decimal-looking floats.
    if sql_type == 'BIGINT':
        return f'CAST({int(value)} AS BIGINT)'
    if sql_type == 'DOUBLE':
        return f'CAST({float(value)!r} AS DOUBLE)'
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    quoted = "'" + text.replace("'", "''") + "'"
    return 'DATE ' + quoted if sql_type == 'DATE' else quoted


def ingest(file, table=None, mode='replace', format='auto', batch_size=500, key=None):
    started = time.monotonic()
    try:
        if mode not in {'replace', 'append', 'upsert'}:
            raise ValueError('mode must be replace, append, or upsert')
        if batch_size < 1:
            raise ValueError('batch-size must be >= 1')
        path = Path(file)
        target = target_table(table, path)
        sql_target = bears.identifier(target)
        detected, schema, rows = read_file(path, format)
        if mode == 'upsert':
            key_cols = [sanitize(k) for k in (key or '').split(',') if k.strip()]
            names = [c for c, _ in schema]
            if not key_cols or any(c not in names for c in key_cols):
                raise ValueError('upsert requires --key matching input columns')
            key_indices = [names.index(c) for c in key_cols]
            # Compare typed SQL values so e.g. CSV integer keys 01 and 1 match.
            # Reverse traversal retains the last occurrences in their file order.
            unique = {}
            for row in reversed(rows):
                row_key = tuple(literal(row[i], schema[i][1]) for i in key_indices)
                if row_key not in unique:
                    unique[row_key] = row
            rows = list(reversed(unique.values()))
            key_columns = ', '.join(f'"{c}"' for c in key_cols)
        guardrail = 'sandbox-write' if target.split('.')[1] in {'raw', 'sandbox'} else 'admin-write'
        columns = ', '.join('"' + c + '"' for c, _ in schema)
        batches = 0
        with wr.trino() as url:
            def execute(statement):
                return bears.sql(url, wr.check_sql_policy(statement, mode=guardrail))
            execute('CREATE SCHEMA IF NOT EXISTS ' + '.'.join(target.split('.')[:2]))
            if mode in {'append', 'upsert'}:
                execute('CREATE TABLE IF NOT EXISTS ' + sql_target + ' (' +
                        ', '.join(f'"{c}" {t}' for c, t in schema) + ')')
            elif not rows:
                execute('CREATE OR REPLACE TABLE ' + sql_target + ' AS SELECT ' +
                        ', '.join(f'CAST(NULL AS {t}) AS "{c}"' for c, t in schema) + ' WHERE FALSE')
            for offset in range(0, len(rows), batch_size):
                values = ', '.join('(' + ', '.join(literal(v, t) for v, (_, t) in zip(row, schema)) + ')'
                                   for row in rows[offset:offset + batch_size])
                if mode == 'upsert':
                    execute(f'CREATE OR REPLACE TABLE {sql_target} AS SELECT {columns} FROM ('
                            f'SELECT {columns}, ROW_NUMBER() OVER (PARTITION BY {key_columns} ORDER BY _src) AS _rn FROM ('
                            f'SELECT {columns}, 1 AS _src FROM (VALUES {values}) AS t({columns}) '
                            f'UNION ALL SELECT {columns}, 2 AS _src FROM {sql_target})) WHERE _rn = 1')
                elif mode == 'replace' and offset == 0:
                    execute(f'CREATE OR REPLACE TABLE {sql_target} AS SELECT * FROM (VALUES {values}) AS t({columns})')
                else:
                    # A column list also supports append into an existing table
                    # whose physical column order differs from the input file.
                    execute(f'INSERT INTO {sql_target} ({columns}) VALUES {values}')
                batches += 1
            audit = bears.audit_table(url, target)
    except Exception:
        wr.telemetry('ingest', int((time.monotonic() - started) * 1000), status='failed', prefix='lake.')
        raise
    duration = int((time.monotonic() - started) * 1000)
    telemetry = wr.telemetry('ingest', duration, status='success', prefix='lake.')
    result = dict(action='ingest', file=str(path), format=detected, mode=mode, table=target,
                columns=[dict(name=c, type=t) for c, t in schema], rows_ingested=len(rows),
                batches=batches, audit=audit, duration_ms=duration, telemetry=telemetry)
    if mode == 'upsert':
        result['key'] = key_cols
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file')
    parser.add_argument('--table')
    parser.add_argument('--mode', choices=['replace', 'append', 'upsert'], default='replace')
    parser.add_argument('--key', default=None)
    parser.add_argument('--format', choices=['auto', 'csv', 'jsonl', 'parquet'], default='auto')
    parser.add_argument('--batch-size', type=int, default=500)
    args = parser.parse_args()
    try:
        print(json.dumps(ingest(**vars(args))))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
