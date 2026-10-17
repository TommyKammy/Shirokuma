"""Ingest local CSV, JSONL, and Parquet files into the resident Iceberg lake."""
import argparse
import base64
import csv
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
import io
import math
from pathlib import Path
import re
import sys
import time
import tempfile
import uuid

import bears_resident as bears
import warehouse_resident as wr

SCHEMAS = set(wr.WRITABLE_SCHEMAS)
FORMATS = {'.csv': 'csv', '.tsv': 'csv', '.jsonl': 'jsonl',
           '.ndjson': 'jsonl', '.json': 'jsonl', '.parquet': 'parquet'}


def sanitize(value):
    name = re.sub(r'[^\w]', '_', str(value).lower()) or 'col'
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


def read_file(path, format='auto', schema_hints=None, encoding=None):
    schema_hints = {} if schema_hints is None else schema_hints
    if not isinstance(schema_hints, dict):
        raise ValueError('schema_hints must be a column-to-type dictionary')
    for sql_type in schema_hints.values():
        if not isinstance(sql_type, str) or not re.fullmatch(
                r'(?:VARCHAR|BIGINT|DOUBLE|BOOLEAN|DATE|TIMESTAMP|DECIMAL(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)',
                sql_type, re.IGNORECASE):
            raise ValueError('unsupported schema hint type: ' + str(sql_type))
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
        raw = path.read_bytes()
        try:
            content = raw.decode(encoding or 'utf-8-sig')
        except UnicodeDecodeError:
            if detected != 'csv' or encoding is not None:
                raise
            content = raw.decode('cp932')
        with io.StringIO(content, newline='') as stream:
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
                rows = (json.loads(content, parse_float=Decimal) if content.lstrip().startswith('[') else
                        [json.loads(line, parse_float=Decimal) for line in content.splitlines() if line.strip()])
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
    schema = [(name, schema_hints.get(column, schema_hints.get(name)) or
               infer_type([row[i] for row in values]))
              for i, (column, name) in enumerate(zip(columns, names))]
    return detected, schema, values


def is_null(value):
    return value is None


def leading_zero(value):
    return isinstance(value, str) and re.fullmatch(r'[+-]?0[0-9]+', value.strip()) is not None


def integer(value):
    return (not isinstance(value, bool) and not leading_zero(value) and
            (isinstance(value, int) or isinstance(value, str) and
             re.fullmatch(r'[+-]?[0-9]+', value.strip()) is not None) and
            -(2**63) <= int(value) < 2**63)


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
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
    values = [v for v in values if not is_null(v) and v != '']
    if not values:
        return 'VARCHAR'
    if all(isinstance(v, bool) or isinstance(v, str) and v.lower() in {'true', 'false'} for v in values):
        return 'BOOLEAN'
    if all(integer(v) for v in values):
        return 'BIGINT'
    if not any(leading_zero(v) for v in values) and all(numeric(v) for v in values):
        return 'DOUBLE'
    if all(is_date(v) for v in values):
        return 'DATE'
    return 'VARCHAR'


def json_text(value):
    """Serialize nested JSON without changing exact decimal numbers into strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k, ensure_ascii=False) + ': ' + json_text(v)
                               for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(json_text(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def literal(value, sql_type):
    sql_type = sql_type.upper()
    if is_null(value) or sql_type != 'VARCHAR' and value == '':
        return f'CAST(NULL AS {sql_type})'
    if sql_type == 'BOOLEAN':
        return 'TRUE' if str(value).lower() == 'true' else 'FALSE'
    # Explicit casts keep CTAS types stable even when the first batch contains
    # only small integers or decimal-looking floats.
    if sql_type == 'BIGINT':
        return f'CAST({int(value)} AS BIGINT)'
    if sql_type == 'DOUBLE':
        return f'CAST({float(value)!r} AS DOUBLE)'
    text = json_text(value) if isinstance(value, (dict, list)) else str(value)
    if sql_type.startswith('DECIMAL'):
        # Avoid normalize(), which can round 38-digit values to the context precision.
        number = Decimal(str(value))
        text = format(number, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        if number == 0:
            text = '0'
    quoted = wr.literal(text)
    if sql_type.startswith('DECIMAL'):
        return f'CAST({quoted} AS {sql_type})'
    if sql_type == 'TIMESTAMP':
        return 'TIMESTAMP ' + quoted
    return 'DATE ' + quoted if sql_type == 'DATE' else quoted


@contextmanager
def input_file(file=None, *, filename=None, content=None):
    """Keep uploaded bytes alive for the duration of a preview or ingestion."""
    if filename is None and content is None:
        if file is None:
            raise ValueError('file or filename and content are required')
        yield Path(file)
        return
    if file is not None:
        raise ValueError('file and uploaded content are mutually exclusive')
    if (not isinstance(filename, str) or not filename.strip()
            or Path(filename).name in {'', '.', '..'}
            or not isinstance(content, (str, bytes))):
        raise ValueError('filename and content (str or bytes) are required')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / Path(filename).name
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        yield path


def preview(file=None, *, filename=None, content=None, format='auto', limit=10,
            schema_hints=None, encoding=None):
    with input_file(file, filename=filename, content=content) as path:
        detected, schema, rows = read_file(path, format, schema_hints, encoding)
    return dict(action='preview', file=str(Path(filename).name if filename else file),
                format=detected, columns=[dict(name=c, type=t) for c, t in schema],
                row_count=len(rows), sample_rows=rows[:max(1, int(limit))])


def upsert_from_stage(execute, sql_target, sql_stage, schema, key_cols, has_rows=True):
    """Merge a loaded stage while retaining the target's column order and types."""
    execute('CREATE TABLE IF NOT EXISTS ' + sql_target + ' (' +
            ', '.join(f'"{c}" {t}' for c, t in schema) + ')')
    if not has_rows:
        return
    existing = [(row[0], row[1]) for row in execute('DESCRIBE ' + sql_target)['rows']]
    existing_names = {name for name, _ in existing}
    if any(c not in existing_names for c in key_cols):
        raise ValueError('upsert keys must exist in the target table')
    merged_schema = existing + [(c, t) for c, t in schema if c not in existing_names]
    def quoted_column(column):
        return '"' + column.replace('"', '""') + '"'
    incoming_names = {c for c, _ in schema}
    # Cast before the join as well as UNION so both use the target's key types.
    incoming = ', '.join(
        f'CAST(t.{quoted_column(c)} AS {t}) AS {quoted_column(c)}'
        for c, t in merged_schema if c in incoming_names)
    updated = ', '.join(
        f'{"t" if c in incoming_names else "old"}.{quoted_column(c)} AS {quoted_column(c)}'
        for c, _ in merged_schema)
    retained = ', '.join(
        f'old.{quoted_column(c)}' if c in existing_names else f'CAST(NULL AS {t})'
        for c, t in merged_schema)
    match = ' AND '.join(f't.{quoted_column(c)} IS NOT DISTINCT FROM old.{quoted_column(c)}'
                         for c in key_cols)
    execute(f'CREATE OR REPLACE TABLE {sql_target} AS '
            f'WITH incoming AS (SELECT {incoming} FROM {sql_stage} t) '
            f'SELECT {updated} FROM incoming t LEFT JOIN {sql_target} old ON {match} '
            f'UNION ALL SELECT {retained} FROM {sql_target} old '
            f'WHERE NOT EXISTS (SELECT 1 FROM incoming t WHERE {match})')


def ingest(file=None, table=None, mode='replace', format='auto', batch_size=500, key=None,
           *, filename=None, content=None, schema_hints=None, encoding=None, atomic_append=False):
    if filename is not None or content is not None:
        with input_file(file, filename=filename, content=content) as path:
            result = ingest(path, table, mode, format, batch_size, key,
                            schema_hints=schema_hints, encoding=encoding, atomic_append=atomic_append)
        result['file'] = Path(filename).name
        return result
    started = time.monotonic()
    try:
        if mode not in {'replace', 'append', 'upsert'}:
            raise ValueError('mode must be replace, append, or upsert')
        if batch_size < 1:
            raise ValueError('batch-size must be >= 1')
        path = Path(file)
        target = target_table(table, path)
        sql_target = wr.identifier(target)
        detected, schema, rows = read_file(path, format, schema_hints, encoding)
        if mode == 'upsert':
            key_cols = [sanitize(k) for k in (key or '').split(',') if k.strip()]
            names = [c for c, _ in schema]
            if not key_cols or any(c not in names for c in key_cols):
                raise ValueError('upsert requires --key matching input columns')
            key_indices = [names.index(c) for c in key_cols]
            # Compare typed SQL values, preserving distinct VARCHAR keys such as 01 and 1.
            # Reverse traversal retains the last occurrences in their file order.
            unique = {}
            for row in reversed(rows):
                row_key = tuple(literal(row[i], schema[i][1]) for i in key_indices)
                if row_key not in unique:
                    unique[row_key] = row
            rows = list(reversed(unique.values()))
        guardrail = 'sandbox-write' if target.split('.')[1] in {'raw', 'sandbox'} else 'admin-write'
        columns = ', '.join('"' + c + '"' for c, _ in schema)
        batches = 0
        with wr.trino() as url:
            def execute(statement):
                return bears.sql(url, wr.check_sql_policy(statement, mode=guardrail))
            execute('CREATE SCHEMA IF NOT EXISTS ' + '.'.join(target.split('.')[:2]))
            sql_stage = (wr.identifier(f'{target}__stage_{uuid.uuid4().hex[:8]}')
                         if mode in {'replace', 'upsert'} or atomic_append else None)
            try:
                load_target = sql_stage or sql_target
                if mode == 'append':
                    execute('CREATE TABLE IF NOT EXISTS ' + sql_target + ' (' +
                            ', '.join(f'"{c}" {t}' for c, t in schema) + ')')
                if sql_stage and not rows:
                    execute('CREATE TABLE ' + sql_stage + ' AS SELECT ' +
                            ', '.join(f'CAST(NULL AS {t}) AS "{c}"' for c, t in schema) + ' WHERE FALSE')
                for offset in range(0, len(rows), batch_size):
                    values = ', '.join('(' + ', '.join(literal(v, t) for v, (_, t) in zip(row, schema)) + ')'
                                       for row in rows[offset:offset + batch_size])
                    if sql_stage and offset == 0:
                        execute(f'CREATE TABLE {sql_stage} AS SELECT * FROM (VALUES {values}) AS t({columns})')
                    else:
                        # Match columns even when the target physical order differs.
                        execute(f'INSERT INTO {load_target} ({columns}) VALUES {values}')
                    batches += 1
                if mode == 'replace':
                    execute(f'CREATE OR REPLACE TABLE {sql_target} AS SELECT * FROM {sql_stage}')
                elif mode == 'upsert':
                    upsert_from_stage(execute, sql_target, sql_stage, schema, key_cols, bool(rows))
                elif mode == 'append' and sql_stage:
                    execute(f'INSERT INTO {sql_target} ({columns}) SELECT {columns} FROM {sql_stage}')
            finally:
                if sql_stage:
                    # The general SQL guardrail rejects DROP. This identifier is
                    # generated here from the validated target, never user SQL.
                    bears.sql(url, f'DROP TABLE IF EXISTS {sql_stage}')
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


def export_table(table, file=None, format='auto', max_rows=None, full=False):
    started = time.monotonic()
    try:
        full_table = bears.qualified_table(table)
        detected = (FORMATS.get(Path(file).suffix.lower()) if file is not None else 'csv') if format == 'auto' else format
        if not isinstance(detected, str) or detected not in {'csv', 'jsonl', 'parquet'}:
            raise ValueError('invalid export format')
        if max_rows is not None and (type(max_rows) is not int or max_rows < 1):
            raise ValueError('max_rows must be a positive integer')
        with wr.trino() as url:
            sql_target = wr.identifier(full_table)
            total_count = bears.sql(url, f'SELECT COUNT(*) FROM {sql_target}')['rows'][0][0]
            limit = f' LIMIT {max_rows}' if max_rows is not None and not full else ''
            # Export must also bypass the resident helper's 1,000-row fetch cap.
            result = wr.execute(url, f'SELECT * FROM {sql_target}{limit}',
                                {'X-Trino-User': 'shirokuma-bears',
                                 'X-Trino-Source': 'shirokuma-bears',
                                 'X-Trino-Catalog': 'iceberg', 'X-Trino-Schema': 'marts'},
                                max_rows=max_rows if limit else math.inf)
        columns = [column['name'] for column in result['columns']]
        rows = result['rows']
        if detected == 'parquet':
            try:
                import pyarrow as arrow
                import pyarrow.parquet as parquet
            except ImportError as exc:
                raise ValueError('parquet export requires pyarrow') from exc
            stream = io.BytesIO()
            parquet.write_table(arrow.table({name: [row[i] for row in rows]
                                             for i, name in enumerate(columns)}), stream)
            raw_bytes = stream.getvalue()
        else:
            if detected == 'csv':
                stream = io.StringIO(newline='')
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows(rows)
                text_content = stream.getvalue()
            else:
                text_content = ''.join(json.dumps(dict(zip(columns, row)), ensure_ascii=False) + '\n' for row in rows)
            raw_bytes = text_content.encode('utf-8')
        if file is not None:
            Path(file).write_bytes(raw_bytes)
            payload = dict(file=str(Path(file)))
        else:
            payload = dict(filename=full_table.split('.')[-1] + '.' + detected)
            payload.update(dict(content_base64=base64.b64encode(raw_bytes).decode('ascii'))
                           if detected == 'parquet' else dict(content=text_content))
    except Exception:
        wr.telemetry('export', int((time.monotonic() - started) * 1000), status='failed', prefix='lake.')
        raise
    duration = int((time.monotonic() - started) * 1000)
    telemetry = wr.telemetry('export', duration, status='success', prefix='lake.')
    return dict(action='export', table=full_table, format=detected, columns=columns,
                row_count=len(rows), total_rows=total_count, truncated=len(rows) < total_count,
                duration_ms=duration, telemetry=telemetry, **payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = sys.argv[1:sys.argv.index('--')] if '--' in sys.argv else sys.argv[1:]
    if any(arg == '--export' or arg.startswith('--export=') for arg in options):
        parser.add_argument('--export', required=True, dest='table')
        parser.add_argument('--output', dest='file')
        parser.add_argument('--format', choices=['auto', 'csv', 'jsonl', 'parquet'], default='auto')
        parser.add_argument('--max-rows', type=int)
        args = parser.parse_args()
        try:
            print(json.dumps(export_table(**vars(args))))
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
    parser.add_argument('file')
    parser.add_argument('--table')
    parser.add_argument('--mode', choices=['replace', 'append', 'upsert'], default='replace')
    parser.add_argument('--key', default=None)
    parser.add_argument('--format', choices=['auto', 'csv', 'jsonl', 'parquet'], default='auto')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--schema-hints', type=json.loads, help='JSON object mapping columns to SQL types')
    parser.add_argument('--encoding', help='input encoding (CSV defaults to UTF-8, then CP932)')
    args = parser.parse_args()
    try:
        print(json.dumps(ingest(**vars(args))))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
