"""Pure fixed Spark/Trino workload and an independent streaming Decimal oracle.

This module is standard-library-only so the host can embed its exact source in
the Spark job. It performs no I/O. SQL is selected by fixed engine/case identities;
no caller-supplied SQL, relation, scale factor, or projection is accepted.

The input is a nine-column, exact-decimal projection of Trino 483's TPCH 1.4
SF0.1 generator. It is not the unmodified TPC-H benchmark. In particular,
q1_sums excludes AVG: Trino and Spark infer different decimal AVG scales.
Both queries explicitly cast their decimal outputs to DECIMAL(28,6).

The transport must independently enforce MAX_SOURCE_BYTES on HTTP bytes.
DatasetOracle also bounds canonical bytes and rows, retaining only six groups,
one previous primary key, and a hash. Its digest includes the workload/schema
header and ordered canonical rows; it is a logical digest, not a Parquet hash.
finish() permits an empty stream for SQL empty-input semantics; the host must
separately require its source validation count to be positive and equal to both
complete source and snapshot streams. Any rejected row poisons that accumulator.

Results use decimal strings with six fractional digits, integers for COUNT,
and JSON null for SQL NULL. Unknown measurements are outside this contract and
must never be replaced by zero. Sample zero is first-observed, not cold-cache.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
import re


SCHEMA_VERSION = 1
WORKLOAD_ID = 'tpch-sf0.1-lineitem-decimal-v1'
SCALE_FACTOR = '0.1'
SOURCE = 'tpch.sf0.1.lineitem'
SOURCE_SQL = 'tpch."sf0.1".lineitem'
UPSTREAM_COMMIT = '50b0b50b75abd47f830b7805ee1b51716eb4065e'
CASES = ('q1_sums', 'q6')
ENGINES = ('trino', 'spark')
SAMPLE_INDICES = (0, 1, 2, 3)
EXPECTED_MEASUREMENTS = len(ENGINES) * len(CASES) * len(SAMPLE_INDICES)
MAX_SOURCE_ROWS = 1_000_000
MAX_SOURCE_BYTES = 256 * 1024**2
SOURCE_COLUMNS = ('orderkey', 'linenumber', 'returnflag', 'linestatus',
                  'quantity', 'extendedprice', 'discount', 'tax', 'shipdate')
SOURCE_COLUMN_TYPES = ('bigint', 'integer', 'varchar', 'varchar',
                       'decimal(12,2)', 'decimal(15,2)', 'decimal(3,2)',
                       'decimal(3,2)', 'date')
RAW_SOURCE_COLUMN_TYPES = ('bigint', 'integer', 'varchar(1)', 'varchar(1)',
                           'double', 'double', 'double', 'double', 'date')
VALIDATION_COLUMNS = ('row_count', 'invalid_identity_rows', 'invalid_numeric_rows')
VALIDATION_COLUMN_TYPES = ('bigint', 'bigint', 'bigint')
RESULT_COLUMNS = {
    'q1_sums': ('returnflag', 'linestatus', 'sum_qty', 'sum_base_price',
                'sum_disc_price', 'sum_charge', 'count_order'),
    'q6': ('revenue',),
}
RESULT_COLUMN_TYPES = {
    'q1_sums': ('varchar', 'varchar', 'decimal(28,6)', 'decimal(28,6)',
                'decimal(28,6)', 'decimal(28,6)', 'bigint'),
    'q6': ('decimal(28,6)',),
}
_DECIMAL_CONTEXT = Context(prec=64, rounding=ROUND_HALF_UP)
_SOURCE_START, _SOURCE_END = date(1992, 1, 1), date(1998, 12, 31)
_Q1_CUTOFF = date(1998, 9, 2)  # DATE '1998-12-01' minus 90 days.
_Q6_START, _Q6_END = date(1994, 1, 1), date(1995, 1, 1)


class WorkloadError(ValueError):
    """A fixed safe code, without source rows or caller-controlled messages."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise WorkloadError(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _engine(engine):
    require(type(engine) is str and engine in ENGINES, 'ENGINE_INVALID')
    return engine


def _case(case_id):
    require(type(case_id) is str and case_id in CASES, 'CASE_INVALID')
    return case_id


def _token(token):
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token), 'RUN_TOKEN_INVALID')
    return token


def _snapshot(snapshot_id):
    require(type(snapshot_id) is int and 0 < snapshot_id < 2**63, 'SNAPSHOT_INVALID')
    return snapshot_id


def table_name(token):
    return 'iceberg.local_experiment.spark_' + _token(token)


def table_location(token):
    return 's3://shirokuma-lakehouse/l1/local_experiment/spark_' + _token(token)


def marker(token):
    return 'shirokuma-local-spark-benchmark:' + _token(token)


def _physical(types, engine):
    _engine(engine)
    return ['string' if engine == 'spark' and t == 'varchar' else
            'int' if engine == 'spark' and t == 'integer' else t for t in types]


def normalize_column_types(engine, column_types):
    """Normalize Trino's ``decimal(p, s)`` spelling, without widening any type.

    QueryResults/SHOW COLUMNS spell decimal types with an ASCII space after the
    comma; Spark simpleString() and these fixed schemas use the compact form.
    Every caller must still compare the returned list to its exact expected
    schema. Unknown types are unchanged, so this helper never approves them.
    """
    _engine(engine)
    require(type(column_types) in (list, tuple) and len(column_types) <= 32
            and all(type(value) is str and 0 < len(value) <= 64 for value in column_types),
            'COLUMN_TYPES_INVALID')
    if engine == 'trino':
        return [re.sub(r'^decimal\(([1-9][0-9]?), ([0-9]{1,2})\)$', r'decimal(\1,\2)', value)
                for value in column_types]
    return list(column_types)


def source_schema(engine='trino'):
    return [{'name': name, 'type': kind}
            for name, kind in zip(SOURCE_COLUMNS, _physical(SOURCE_COLUMN_TYPES, engine))]


def result_schema(case_id, engine='trino'):
    _case(case_id)
    return [{'name': name, 'type': kind}
            for name, kind in zip(RESULT_COLUMNS[case_id], _physical(RESULT_COLUMN_TYPES[case_id], engine))]


def source_select_sql():
    """Only the normalized SELECT body; the host owns CREATE/INSERT and identity."""
    return ('SELECT orderkey, linenumber, CAST(returnflag AS VARCHAR) AS returnflag, '
            'CAST(linestatus AS VARCHAR) AS linestatus, '
            'CAST(quantity AS DECIMAL(12,2)) AS quantity, '
            'CAST(extendedprice AS DECIMAL(15,2)) AS extendedprice, '
            'CAST(discount AS DECIMAL(3,2)) AS discount, '
            'CAST(tax AS DECIMAL(3,2)) AS tax, shipdate FROM ' + SOURCE_SQL)


def source_rows_sql():
    return source_select_sql() + ' ORDER BY orderkey, linenumber'


def source_validation_sql():
    """Validate raw DOUBLE inputs before a lossy cast could hide bad source data.

    TRY_CAST protects the validation query from overflow/non-finite values;
    IS DISTINCT FROM makes a failed cast count as invalid, never unknown.
    The caller also verifies the raw selected column types against the fixed
    RAW_SOURCE_COLUMN_TYPES and requires row_count in [1, MAX_SOURCE_ROWS].
    """
    identity = ('orderkey IS NULL OR orderkey <= 0 OR linenumber IS NULL OR '
                'linenumber NOT BETWEEN 1 AND 7 OR returnflag IS NULL OR '
                "returnflag NOT IN ('A', 'N', 'R') OR linestatus IS NULL OR "
                "linestatus NOT IN ('F', 'O') OR shipdate IS NULL OR "
                "shipdate NOT BETWEEN DATE '1992-01-01' AND DATE '1998-12-31'")
    numeric = []
    for name, precision in (('quantity', 12), ('extendedprice', 15), ('discount', 3), ('tax', 3)):
        cast = f'TRY_CAST({name} AS DECIMAL({precision},2))'
        numeric.append(f'({name} IS NULL OR NOT is_finite({name}) OR {cast} IS NULL OR '
                       f'CAST({cast} AS DOUBLE) IS DISTINCT FROM {name})')
    numeric += ['quantity NOT BETWEEN 1 AND 50', 'quantity <> floor(quantity)',
                'extendedprice <= 0', 'discount NOT BETWEEN 0 AND 0.10',
                'tax NOT BETWEEN 0 AND 0.08']
    return ('SELECT count(*) AS row_count, count_if(' + identity + ') AS invalid_identity_rows, '
            'count_if(' + ' OR '.join(numeric) + ') AS invalid_numeric_rows FROM ' + SOURCE_SQL)


def _relation(engine, token, snapshot_id):
    _engine(engine)
    name, snapshot_id = 'spark_' + _token(token), _snapshot(snapshot_id)
    if engine == 'trino':
        return f'"iceberg"."local_experiment"."{name}" FOR VERSION AS OF {snapshot_id}'
    return f'`iceberg`.`local_experiment`.`{name}` VERSION AS OF {snapshot_id}'


def table_rows_sql(token, snapshot_id, *, engine='trino'):
    return 'SELECT ' + ', '.join(SOURCE_COLUMNS) + ' FROM ' + _relation(engine, token, snapshot_id) + \
           ' ORDER BY orderkey, linenumber'


def query_sql(engine, case_id, token, snapshot_id):
    """A fixed SELECT against an explicitly pinned snapshot, never current head."""
    _case(case_id)
    relation = _relation(engine, token, snapshot_id)
    if case_id == 'q1_sums':
        return ("SELECT returnflag, linestatus, "
                "CAST(sum(quantity) AS DECIMAL(28,6)) AS sum_qty, "
                "CAST(sum(extendedprice) AS DECIMAL(28,6)) AS sum_base_price, "
                "CAST(sum(extendedprice * (CAST('1.00' AS DECIMAL(3,2)) - discount)) AS DECIMAL(28,6)) AS sum_disc_price, "
                "CAST(sum(extendedprice * (CAST('1.00' AS DECIMAL(3,2)) - discount) * (CAST('1.00' AS DECIMAL(3,2)) + tax)) "
                "AS DECIMAL(28,6)) AS sum_charge, count(*) AS count_order FROM " + relation +
                " WHERE shipdate <= DATE '1998-09-02' GROUP BY returnflag, linestatus "
                "ORDER BY returnflag, linestatus")
    return ("SELECT CAST(sum(extendedprice * discount) AS DECIMAL(28,6)) AS revenue FROM " + relation +
            " WHERE shipdate >= DATE '1994-01-01' AND shipdate < DATE '1995-01-01' "
            "AND discount BETWEEN CAST('0.05' AS DECIMAL(3,2)) AND CAST('0.07' AS DECIMAL(3,2)) "
            "AND quantity < CAST('24.00' AS DECIMAL(4,2))")


def _decimal(value, precision, scale):
    require(type(value) in (str, Decimal), 'DECIMAL_VALUE_INVALID')
    if type(value) is str:
        require(len(value) <= 64 and re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value),
                'DECIMAL_VALUE_INVALID')
        value = Decimal(value)
    require(value.is_finite() and len(value.as_tuple().digits) <= 64, 'DECIMAL_VALUE_INVALID')
    require(-64 <= value.as_tuple().exponent <= 64, 'DECIMAL_VALUE_INVALID')
    with localcontext(_DECIMAL_CONTEXT):
        require(abs(value) < Decimal(10) ** (precision - scale), 'DECIMAL_PRECISION_INVALID')
        try:
            rounded = value.quantize(Decimal(1).scaleb(-scale))
        except InvalidOperation:
            raise WorkloadError('DECIMAL_VALUE_INVALID') from None
        require(rounded == value, 'DECIMAL_SCALE_INVALID')
        # Decimal signed zero has no different SQL meaning; canonicalize it.
        return abs(rounded) if rounded == 0 else rounded


def _source_row(row):
    require(type(row) is list and len(row) == len(SOURCE_COLUMNS), 'SOURCE_ROW_SHAPE_INVALID')
    orderkey, linenumber, flag, status = row[:4]
    require(type(orderkey) is int and 0 < orderkey < 2**63 and type(linenumber) is int
            and 1 <= linenumber <= 7, 'SOURCE_KEY_INVALID')
    require(type(flag) is str and flag in ('A', 'N', 'R') and type(status) is str and status in ('F', 'O'),
            'SOURCE_CATEGORY_INVALID')
    q, p, d, t = [_decimal(value, precision, 2) for value, precision in zip(row[4:8], (12, 15, 3, 3))]
    require(1 <= q <= 50 and q == q.to_integral_value() and p > 0
            and 0 <= d <= Decimal('0.10') and 0 <= t <= Decimal('0.08'), 'SOURCE_NUMERIC_DOMAIN_INVALID')
    day = row[8]
    if type(day) is str:
        require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day), 'SOURCE_DATE_INVALID')
        try:
            day = date.fromisoformat(day)
        except ValueError:
            raise WorkloadError('SOURCE_DATE_INVALID') from None
    require(type(day) is date and _SOURCE_START <= day <= _SOURCE_END, 'SOURCE_DATE_INVALID')
    canonical = [orderkey, linenumber, flag, status, *[format(x, '.2f') for x in (q, p, d, t)], day.isoformat()]
    return canonical, (q, p, d, t), day


def normalize_result(case_id, engine, columns, column_types, rows):
    """Strictly validate physical types, then return one cross-engine JSON shape.

    Decimal floats are refused rather than rounded back into apparently exact
    values. Q1 keys are sorted for a logical result hash; duplicates are refused.
    Its decimal NULLs are preserved (and must match the independent oracle).
    """
    physical = result_schema(case_id, engine)
    require(type(columns) in (list, tuple) and list(columns) == [x['name'] for x in physical],
            'RESULT_COLUMNS_INVALID')
    require(normalize_column_types(engine, column_types) == [x['type'] for x in physical],
            'RESULT_TYPES_INVALID')
    require(type(rows) is list and (len(rows) <= 6 if case_id == 'q1_sums' else len(rows) == 1),
            'RESULT_ROWS_INVALID')
    normalized, seen = [], set()
    for row in rows:
        require(type(row) in (list, tuple) and len(row) == len(physical), 'RESULT_ROW_SHAPE_INVALID')
        if case_id == 'q1_sums':
            flag, status = row[:2]
            require(type(flag) is str and flag in ('A', 'N', 'R') and type(status) is str and status in ('F', 'O'),
                    'RESULT_CATEGORY_INVALID')
            require((flag, status) not in seen, 'RESULT_DUPLICATE_GROUP')
            seen.add((flag, status))
            count = row[-1]
            require(type(count) is int and 0 < count <= MAX_SOURCE_ROWS, 'RESULT_COUNT_INVALID')
            values = [None if x is None else format(_decimal(x, 28, 6), '.6f') for x in row[2:-1]]
            normalized.append([flag, status, *values, count])
        else:
            normalized.append([None if row[0] is None else format(_decimal(row[0], 28, 6), '.6f')])
    if case_id == 'q1_sums':
        normalized.sort(key=lambda row: (row[0], row[1]))
        require(sum(row[-1] for row in normalized) <= MAX_SOURCE_ROWS, 'RESULT_COUNT_INVALID')
    schema = result_schema(case_id, 'spark')  # SQL VARCHAR and Spark STRING share this logical type.
    material = {'schema': schema, 'rows': normalized}
    return {**material, 'row_count': len(normalized), 'sha256': hashlib.sha256(_canonical(material)).hexdigest()}


class DatasetOracle:
    """One-pass exact result oracle and ordered logical hash; never saves rows."""
    def __init__(self):
        header = _canonical({'workload_id': WORKLOAD_ID, 'schema': source_schema('spark')}) + b'\n'
        self._hash = hashlib.sha256(header)
        self._bytes = len(header)
        self._rows = 0
        self._previous = None
        self._groups = {}
        self._revenue = None
        self._failed = False
        self._finished = None

    def add(self, row):
        require(self._finished is None, 'ORACLE_CLOSED')
        require(not self._failed, 'ORACLE_FAILED')
        try:
            require(self._rows < MAX_SOURCE_ROWS, 'SOURCE_ROW_LIMIT')
            canonical, (quantity, price, discount, tax), day = _source_row(row)
            key = tuple(canonical[:2])
            require(self._previous is None or key > self._previous, 'SOURCE_ORDER_OR_DUPLICATE_INVALID')
            encoded = _canonical(canonical) + b'\n'
            require(self._bytes + len(encoded) <= MAX_SOURCE_BYTES, 'SOURCE_BYTE_LIMIT')
            with localcontext(_DECIMAL_CONTEXT):
                if day <= _Q1_CUTOFF:
                    group_key = tuple(canonical[2:4])
                    group = self._groups.setdefault(group_key, [Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0])
                    group[0] += quantity
                    group[1] += price
                    group[2] += price * (Decimal(1) - discount)
                    group[3] += price * (Decimal(1) - discount) * (Decimal(1) + tax)
                    group[4] += 1
                if _Q6_START <= day < _Q6_END and Decimal('0.05') <= discount <= Decimal('0.07') and quantity < 24:
                    self._revenue = (self._revenue if self._revenue is not None else Decimal(0)) + price * discount
            self._hash.update(encoded)
            self._bytes += len(encoded)
            self._rows += 1
            self._previous = key
        except BaseException:
            self._failed = True
            raise

    def finish(self):
        require(not self._failed, 'ORACLE_FAILED')
        if self._finished is None:
            q1 = [[*key, *values] for key, values in sorted(self._groups.items())]
            expected = {}
            for case_id, rows in [('q1_sums', q1), ('q6', [[self._revenue]])]:
                expected[case_id] = normalize_result(case_id, 'trino', RESULT_COLUMNS[case_id],
                                                     RESULT_COLUMN_TYPES[case_id], rows)
            self._finished = {'row_count': self._rows, 'logical_sha256': self._hash.hexdigest(), 'expected': expected}
        return deepcopy(self._finished)
