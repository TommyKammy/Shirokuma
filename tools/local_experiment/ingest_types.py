"""Value classification and conversion helpers shared by the ingest paths."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import math
import re

INTEGER_TYPES = {'INTEGER', 'INT', 'SMALLINT', 'TINYINT', 'BIGINT'}


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


def integer_value(value, sql_type):
    """Convert integers exactly, with the same bounds in every ingestion path."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid ' + sql_type + ' value') from exc
    bits = {'TINYINT': 8, 'SMALLINT': 16, 'INTEGER': 32, 'INT': 32, 'BIGINT': 64}[sql_type.upper()]
    if (not number.is_finite() or number != number.to_integral_value()
            or not -(2**(bits - 1)) <= number < 2**(bits - 1)):
        raise ValueError('invalid ' + sql_type + ' value')
    return int(number)


def timestamp_value(value, sql_type):
    """Preserve fractional digits; reject nonzero digits beyond target precision."""
    match = re.fullmatch(r'TIMESTAMP(?:\((\d+)\))?', sql_type.upper())
    if not match or match[1] is not None and not 0 <= int(match[1]) <= 6:
        raise ValueError('unsupported timestamp type: ' + sql_type)
    # Iceberg stores microseconds. Reject finer values instead of rounding them.
    precision = int(match[1]) if match[1] is not None else 6
    text = str(value).replace('T', ' ')
    parts = re.fullmatch(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d{1,12}))?', text)
    if not parts:
        raise ValueError('timestamp must be timezone naive ISO date and time')
    datetime.fromisoformat(parts[1])
    fraction = parts[2] or ''
    if any(d != '0' for d in fraction[precision:]):
        raise ValueError('timestamp does not fit ' + sql_type)
    fraction = fraction[:precision]
    return parts[1] + ('.' + fraction if fraction else '')
