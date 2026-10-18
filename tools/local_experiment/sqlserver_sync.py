"""Typed SQL Server batches and full-table reconciliation with resident Iceberg.

Offline JSON format: {"columns": [{"name": "id", "type": "INT"},
{"name": "amount", "type": "DECIMAL", "precision": 18, "scale": 2}],
"rows": [{"id": 1, "amount": "1250.50"}]}.
Decimals must be strings in JSON. SQLite mocks use declared column types;
store exact decimals as TEXT (SQLite numeric affinity can round decimals).
Reconciliation compares whole tables, including for append/upsert syncs.
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime
from decimal import Decimal, localcontext
import importlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import uuid

import lake_ingest
import warehouse_resident as wr


def identifier(value, quote='"'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise ValueError('expected a simple SQL identifier: ' + str(value))
    return '[' + value + ']' if quote == '[' else '"' + value + '"'


def source_name(table):
    parts = table.split('.')
    if not 1 <= len(parts) <= 2:
        raise ValueError('source table must be [schema.]table')
    return '.'.join(identifier(part, '[') for part in parts)


def type_hint(column):
    declared = column['type'].upper().strip()
    match = re.fullmatch(r'(\w+)(?:\((\d+)(?:,\s*(\d+))?\))?', declared)
    if not match:
        raise ValueError('unsupported source type: ' + declared)
    kind, precision, scale = match.groups()
    if kind in {'INT', 'INTEGER', 'BIGINT', 'SMALLINT', 'TINYINT'}:
        return 'BIGINT'
    if kind in {'DECIMAL', 'NUMERIC', 'MONEY', 'SMALLMONEY'}:
        p = int(column.get('precision') or precision or (10 if kind == 'SMALLMONEY' else 19 if kind == 'MONEY' else 18))
        s = int(column.get('scale') if column.get('scale') is not None else scale or (4 if kind in {'MONEY', 'SMALLMONEY'} else 0))
        if not 0 <= s <= p <= 38 or p < 1:
            raise ValueError('invalid decimal precision/scale')
        return f'DECIMAL({p},{s})'
    if kind in {'VARCHAR', 'NVARCHAR', 'CHAR', 'NCHAR', 'TEXT'}:
        return 'VARCHAR'
    if kind == 'DATE':
        return 'DATE'
    if kind in {'DATETIME', 'DATETIME2'}:
        return 'TIMESTAMP'
    if kind == 'BIT':
        return 'BOOLEAN'
    raise ValueError('unsupported source type: ' + declared)


def convert(value, hint):
    if value is None:
        return None
    if hint.startswith('DECIMAL'):
        if isinstance(value, (float, bool)):
            raise ValueError('decimal values must be exact strings or Decimal, not floats')
        number = Decimal(value)
        p, s = map(int, re.findall(r'\d+', hint))
        with localcontext() as ctx:
            ctx.prec = max(80, p + s + 1)
            if (not number.is_finite() or abs(number) >= Decimal(10) ** (p - s)
                    or number != number.quantize(Decimal(1).scaleb(-s))):
                raise ValueError('decimal does not fit ' + hint)
        return format(number, 'f')
    if hint in lake_ingest.INTEGER_TYPES:
        number = Decimal(str(value))
        bits = {'TINYINT': 8, 'SMALLINT': 16, 'INTEGER': 32, 'INT': 32, 'BIGINT': 64}[hint]
        if (not number.is_finite() or number != number.to_integral_value()
                or not -(2**(bits - 1)) <= number < 2**(bits - 1)):
            raise ValueError('invalid ' + hint + ' value')
        return int(number)
    if hint == 'BOOLEAN':
        if str(value).lower() not in {'true', 'false', '0', '1'}:
            raise ValueError('invalid BIT value')
        return str(value).lower() in {'true', '1'}
    if hint == 'DATE':
        return date.fromisoformat(str(value)).isoformat()
    if hint == 'TIMESTAMP':
        high_precision = isinstance(value, str) and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\.\d{7}', value)
        timestamp = datetime.fromisoformat(value[:-1] if high_precision else str(value))
        if timestamp.tzinfo is not None:
            raise ValueError('SQL Server DATETIME must be timezone naive')
        # datetime only retains microseconds; DATETIME2 strings can carry a
        # seventh fractional digit. Keep it for Trino's timestamp literal.
        if high_precision:
            return value.replace('T', ' ')
        return timestamp.isoformat(sep=' ')
    return str(value)


class Source:
    """DB-API cursor or deterministic JSON source; caller owns connection lifetime."""
    def __init__(self, columns, cursor=None, rows=None, connection=None, table=None):
        self.columns, self.cursor, self.rows = columns, cursor, rows
        self.connection, self.table = connection, table
        if not columns:
            raise ValueError('source has no columns')
        self.hints = {}
        for column in columns:
            name = column['name']
            identifier(name)
            normalized = lake_ingest.sanitize(name)
            if normalized in self.hints:
                raise ValueError('source column names collide after normalization')
            self.hints[normalized] = type_hint(column)

    def batches(self, batch_size=500):
        if batch_size < 1:
            raise ValueError('batch-size must be >= 1')
        offset = 0
        while True:
            rows = (self.cursor.fetchmany(batch_size) if self.cursor is not None else
                    self.rows[offset:offset + batch_size])
            if not rows:
                break
            offset += len(rows)
            converted = []
            for row in rows:
                values = [row.get(c['name']) for c in self.columns] if isinstance(row, dict) else row
                if len(values) != len(self.columns):
                    raise ValueError('source row width does not match schema')
                converted.append(dict(zip(self.hints, (convert(v, h) for v, h in zip(values, self.hints.values())))))
            yield converted

    def aggregate_metrics(self, key, amount_col):
        """Let SQL Server apply its native COUNT/DISTINCT/SUM semantics."""
        expressions = ['COUNT_BIG(*)']
        names = ['row_count']
        if key:
            expressions.append('COUNT_BIG(DISTINCT ' + identifier(key, '[') + ')')
            names.append('distinct_keys')
        if amount_col:
            expressions.append('CAST(SUM(' + identifier(amount_col, '[') + ') AS VARCHAR(100))')
            names.append('sum_amount')
        for column in (c['name'] for c in self.columns):
            expressions.append('COUNT_BIG(*) - COUNT_BIG(' + identifier(column, '[') + ')')
            names.append('null_counts.' + lake_ingest.sanitize(column))
        cursor = self.connection.cursor()
        try:
            cursor.execute('SELECT ' + ', '.join(expressions) + ' FROM ' + self.table)
            row = cursor.fetchone()
            if row is None or len(row) != len(names):
                raise RuntimeError('invalid source aggregate result')
            result = dict(null_counts={})
            for name, value in zip(names, row):
                if name.startswith('null_counts.'):
                    result['null_counts'][name.removeprefix('null_counts.')] = value
                else:
                    result[name] = value
            return result
        finally:
            cursor.close()


@contextmanager
def open_source(source_table, *, host=None, port=1433, database=None, user=None,
                password=None, mock_source=None, read_rows=True):
    table = source_name(source_table)
    if mock_source and Path(mock_source).suffix.lower() == '.json':
        payload = json.loads(Path(mock_source).read_text(encoding='utf-8'), parse_float=Decimal)
        yield Source(payload['columns'], rows=payload['rows'])
        return
    connection = None
    try:
        if mock_source:
            connection = sqlite3.connect(Path(mock_source).resolve().as_uri() + '?mode=ro', uri=True)
            metadata = connection.execute('PRAGMA table_info(' + identifier(source_table) + ')').fetchall()
            columns = [dict(name=row[1], type=row[2]) for row in metadata]
        else:
            if not all((host, database, user, password)):
                raise ValueError('host, database, user and password are required without --mock-source')
            try:
                driver = importlib.import_module('pymssql')
            except ImportError:
                try:
                    driver = importlib.import_module('pyodbc')
                except ImportError as exc:
                    raise RuntimeError('install pymssql or pyodbc, or use --mock-source') from exc
                def odbc(value):
                    return '{' + str(value).replace('}', '}}') + '}'
                connection = driver.connect('DRIVER={ODBC Driver 18 for SQL Server};' +
                    ';'.join(k + '=' + odbc(v) for k, v in
                             [('SERVER', f'{host},{port}'), ('DATABASE', database), ('UID', user), ('PWD', password)]))
            else:
                connection = driver.connect(server=host, port=port, database=database,
                                            user=user, password=password)
            schema, name = source_table.split('.') if '.' in source_table else ('dbo', source_table)
            cursor = connection.cursor()
            # Literals are escaped independently of the driver's parameter style.
            cursor.execute('SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE '
                           'FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ' + wr.literal(schema) +
                           ' AND TABLE_NAME = ' + wr.literal(name) + ' ORDER BY ORDINAL_POSITION')
            columns = [dict(name=r[0], type=r[1], precision=r[2], scale=r[3]) for r in cursor.fetchall()]
            cursor.close()
        cursor = connection.cursor()
        try:
            if read_rows or mock_source:
                cursor.execute('SELECT * FROM ' + table)
            yield Source(columns, cursor=cursor, connection=None if mock_source else connection, table=table)
        finally:
            cursor.close()
    finally:
        if connection is not None:
            connection.close()


class BatchMetrics:
    """Accumulate the manifest while consuming the extracted rows once."""
    def __init__(self, hints, key=None, amount_col=None):
        self.key = key.lower() if key else None
        self.amount = amount_col.lower() if amount_col else None
        for column in (self.key, self.amount):
            if column and column not in hints:
                raise ValueError('reconciliation column not found in source')
        if self.amount and not (hints[self.amount].startswith('DECIMAL') or hints[self.amount] in lake_ingest.INTEGER_TYPES):
            raise ValueError('amount column must be an integer or decimal')
        self.hints = hints
        self.metrics = dict(row_count=0, null_counts={c: 0 for c in hints})
        self.keys, self.total, self.amounts = set(), Decimal(0), 0

    def add(self, row):
        self.metrics['row_count'] += 1
        for column in self.hints:
            self.metrics['null_counts'][column] += row[column] is None
        if self.key and row[self.key] is not None:
            value = row[self.key]
            self.keys.add(Decimal(value) if self.hints[self.key].startswith('DECIMAL') else value)
        if self.amount and row[self.amount] is not None:
            with localcontext() as ctx:
                ctx.prec = 100
                self.total += Decimal(row[self.amount])
            self.amounts += 1

    def finish(self):
        if self.key:
            self.metrics['distinct_keys'] = len(self.keys)
        if self.amount:
            self.metrics['sum_amount'] = format(self.total, 'f') if self.amounts else None
        return self.metrics


def extract(source_table, target_table, *, mode='replace', key=None, amount_col=None, batch_size=500,
            target_types=None, **connection):
    target = lake_ingest.target_table(target_table, Path('source.csv'))
    if mode not in {'replace', 'append', 'upsert'}:
        raise ValueError('mode must be replace, append, or upsert')
    with open_source(source_table, **connection) as source, tempfile.TemporaryDirectory() as directory:
        if key:
            identifier(key)
            if key.lower() not in source.hints:
                raise ValueError('key not found in source')
        if mode == 'upsert' and not key:
            raise ValueError('upsert requires --key')
        hints = dict(source.hints)
        for column, kind in (target_types or {}).items():
            if column not in hints:
                continue
            kind = kind.upper().strip()
            if not re.fullmatch(r'(?:VARCHAR|INTEGER|INT|SMALLINT|TINYINT|BIGINT|BOOLEAN|DATE|TIMESTAMP|DECIMAL\(\d+,\s*\d+\))', kind):
                raise ValueError('unsupported target type for ' + column + ': ' + kind)
            hints[column] = kind
        extraction = BatchMetrics(source.hints, key, amount_col)
        manifest = BatchMetrics(hints, key, amount_col)
        unique = {}
        path = Path(directory) / 'source.csv'
        # JSONL retains NULL versus empty string, unlike CSV ingestion.
        path = path.with_suffix('.jsonl')
        count = 0
        with path.open('w', encoding='utf-8') as stream:
            for batch in source.batches(batch_size):
                for row in batch:
                    extraction.add(row)
                    row = {c: convert(v, hints[c]) for c, v in row.items()}
                    if mode == 'upsert':
                        unique[lake_ingest.key_value(row[key.lower()], hints[key.lower()])] = row
                    else:
                        manifest.add(row)
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                count += len(batch)
            if not count:
                # A header-only CSV conveys schema for an empty source.
                path = path.with_suffix('.csv')
                with path.open('w', encoding='utf-8', newline='') as empty:
                    csv.writer(empty).writerow(source.hints)
        for row in unique.values():
            manifest.add(row)
        result = lake_ingest.ingest(path, table=target, mode=mode, key=key,
                                   batch_size=batch_size, schema_hints=hints,
                                   atomic_append=(mode == 'append'))
        return dict(source_table=source_table, target_table=target, row_count=count,
                    schema_hints=hints, metrics=manifest.finish(),
                    extraction_metrics=extraction.finish(), ingest=result)


class ReconciliationError(RuntimeError):
    def __init__(self, report):
        self.report = report
        super().__init__('SQL Server / Iceberg reconciliation mismatch')


def reconcile(source_table, target_table, *, key=None, amount_col=None, extracted_metrics=None, **connection):
    target = lake_ingest.target_table(target_table, Path('source.csv'))
    columns = list(dict.fromkeys(c for c in (key, amount_col) if c))
    for column in columns:
        identifier(column)
    if extracted_metrics is None:
        with open_source(source_table, read_rows=False, **connection) as source:
            manifest = BatchMetrics(source.hints, key, amount_col)
            if source.connection is not None:
                metrics = source.aggregate_metrics(key, amount_col)
            else:
                for batch in source.batches():
                    for row in batch:
                        manifest.add(row)
                metrics = manifest.finish()
    else:
        metrics = extracted_metrics
    target_metrics = table_metrics(target, metrics['null_counts'], key, amount_col)
    discrepancies = []
    for name, expected in metrics.items():
        pairs = ((('null_counts.' + c, v, target_metrics['null_counts'][c])
                  for c, v in expected.items()) if name == 'null_counts' else
                 [(name, expected, target_metrics[name])])
        for metric, expected, value in pairs:
            matched = (expected == value if metric != 'sum_amount' or expected is None or value is None
                       else Decimal(expected) == Decimal(str(value)))
            if not matched:
                discrepancies.append(dict(metric=metric, source=expected, target=value))
    report = dict(status='mismatch' if discrepancies else 'matched', source_table=source_table,
                  target_table=target, metrics=dict(source=metrics, target=target_metrics),
                  discrepancies=discrepancies)
    if discrepancies:
        raise ReconciliationError(report)
    return report


def table_metrics(target, columns, key=None, amount_col=None):
    """Read metrics from the actual typed table before it is published."""
    expressions = ['COUNT(*)']
    names = ['row_count']
    if key:
        expressions.append('COUNT(DISTINCT ' + identifier(key.lower()) + ')')
        names.append('distinct_keys')
    if amount_col:
        # Trino JSON decoding must never turn an exact decimal into a float.
        expressions.append('CAST(SUM(' + identifier(amount_col.lower()) + ') AS VARCHAR)')
        names.append('sum_amount')
    for column in columns:
        expressions.append('COUNT(*) - COUNT(' + identifier(column.lower()) + ')')
        names.append('null_counts.' + column)
    with wr.trino() as url:
        result = wr.execute(url, 'SELECT ' + ', '.join(expressions) + ' FROM ' + wr.identifier(target),
                            {'X-Trino-User': 'shirokuma', 'X-Trino-Catalog': 'iceberg'})
    rows = result.get('rows', [])
    if result.get('truncated') or len(rows) != 1 or len(rows[0]) != len(names):
        raise RuntimeError('invalid target aggregate result')
    target_metrics = dict(null_counts={})
    for name, value in zip(names, rows[0]):
        column = name.removeprefix('null_counts.')
        if name.startswith('null_counts.'):
            target_metrics['null_counts'][column] = value
        else:
            target_metrics[name] = value
    return target_metrics


def sync(source_table, target_table, *, mode='replace', key=None, amount_col=None, **connection):
    target = lake_ingest.target_table(target_table, Path('source.csv'))
    if mode not in {'replace', 'append', 'upsert'}:
        raise ValueError('mode must be replace, append, or upsert')
    if mode == 'upsert' and not key:
        raise ValueError('upsert requires --key')
    stage = target + '__sync_stage_' + uuid.uuid4().hex
    candidate = stage + '__candidate'
    sql_target, sql_stage = wr.identifier(target), wr.identifier(stage)
    sql_candidate = wr.identifier(candidate)
    with wr.trino() as url:
        def execute(statement):
            return lake_ingest.bears.sql(url, wr.check_sql_policy(statement, mode='admin-write'))
        cleanup = [sql_stage]
        try:
            target_types = {}
            if mode != 'replace':
                metadata = execute('SELECT column_name, data_type FROM iceberg.information_schema.columns '
                    'WHERE table_schema = ' + wr.literal(target.split('.')[1]) +
                    ' AND table_name = ' + wr.literal(target.split('.')[2]))
                target_types = dict(metadata['rows'])
            extracted = extract(source_table, stage, mode='upsert' if mode == 'upsert' else 'replace',
                                key=key, amount_col=amount_col, target_types=target_types, **connection)
            report = reconcile(source_table, stage, key=key, amount_col=amount_col,
                               extracted_metrics=extracted['metrics'])
            schema = list(extracted['schema_hints'].items())
            publish = sql_stage
            if target_types:
                cleanup.append(sql_candidate)
                execute(f'CREATE TABLE {sql_candidate} AS SELECT * FROM {sql_target}')
                if mode == 'append':
                    columns = ', '.join(identifier(c) for c, _ in schema)
                    execute(f'INSERT INTO {sql_candidate} ({columns}) SELECT {columns} FROM {sql_stage}')
                else:
                    lake_ingest.upsert_from_stage(execute, sql_candidate, sql_stage, schema,
                                                 [lake_ingest.sanitize(key)], bool(extracted['row_count']))
                publish = sql_candidate
            candidate_metrics = table_metrics(candidate if target_types else stage,
                                              dict(schema), key, amount_col)
            execute(f'CREATE OR REPLACE TABLE {sql_target} AS SELECT * FROM {publish}')
            return dict(report, target_table=target, reconciliation_scope='normalized_extraction_batch',
                        extraction_metrics=extracted['extraction_metrics'],
                        final_candidate_metrics=candidate_metrics)
        finally:
            for sql_table in reversed(cleanup):
                try:
                    lake_ingest.bears.sql(url, f'DROP TABLE IF EXISTS {sql_table}')
                except Exception:
                    logging.getLogger(__name__).warning('Failed to drop sync staging table %s', sql_table)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('extract', 'reconcile', 'sync'):
        sub = commands.add_parser(command)
        for name in ('source-table', 'target-table'):
            sub.add_argument('--' + name, required=True)
        for name in ('host', 'database', 'user', 'password', 'key', 'mock-source'):
            sub.add_argument('--' + name)
        sub.add_argument('--port', type=int, default=1433)
        if command in {'extract', 'sync'}:
            sub.add_argument('--mode', choices=('replace', 'append', 'upsert'), default='replace')
        if command in {'reconcile', 'sync'}:
            sub.add_argument('--amount-col')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        report = globals()[command](**args)
    except ReconciliationError as exc:
        print(json.dumps(exc.report, ensure_ascii=False))
        return 1
    except Exception as exc:
        # Connection errors can include credentials; do not echo their messages.
        print(json.dumps(dict(status='error', error=type(exc).__name__)), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
