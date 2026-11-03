"""Latest saved failed Pawprint records, globally uncorrelated metadata only.

Producer DateTime64(3) strings use a space and documented UTC. Normalize to
ISO UTC; event age is unrestricted. UInt64 duration stays exact decimal text.
ReplacingMergeTree may return repeated event IDs; preserve source records.
"""
from datetime import datetime, timezone
import json
import re

from agenthouse import manage
from audit_service import TELEMETRY_FIELDS
from db_platform_observations import _authority, _now
from warehouse_operations import OperationError

SQL = ('SELECT ' + ','.join(TELEMETRY_FIELDS) +
       " FROM agenthouse.pawprints WHERE status='failed'"
       ' ORDER BY timestamp DESC,event_id DESC LIMIT 21')
MAX_DTO_BYTES = 65536
UINT64 = 2**64 - 1
COVERAGE = dict(kind='recent_failed_pawprints_metadata', limit=20,
    server_filtered=True, database_correlation=False, raw_logs=False,
    health_check=False, policy_audit=False, complete_history=False)


def _string(value):
    # Empty String columns (including trace_id) are legitimate schema values.
    if (type(value) is not str or len(value) > 256
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError()
    return value


def _timestamp(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}', value, re.ASCII):
        raise ValueError()
    stamp = datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=timezone.utc)
    return stamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _duration(value):
    if type(value) is str:
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})', value, re.ASCII):
            raise ValueError()
        value = int(value)
    if type(value) is not int or not 0 <= value <= UINT64:
        raise ValueError()
    return str(value)


def _records(rows):
    if type(rows) is not list or len(rows) > 21:
        raise ValueError()
    result = []
    for row in rows:
        if (type(row) is not dict or not 8 <= len(row) <= 32
                or any(field not in row for field in TELEMETRY_FIELDS)):
            raise ValueError()
        item = {field: (_timestamp(row[field]) if field == 'timestamp' else
                        _duration(row[field]) if field == 'duration_ms' else _string(row[field]))
                for field in TELEMETRY_FIELDS}
        if item['status'] != 'failed':
            raise ValueError()
        result.append(item)
    return result


def observe(principal, role):
    fingerprint = _authority(principal, role)  # Before Client initialization.
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin',
        observed_at=_now(), source='agenthouse_pawprints', availability='unavailable',
        code='RECENT_FAILURES_UNAVAILABLE', coverage=dict(COVERAGE), items=[], truncated=None)
    try:
        rows = _records(manage.Client().rows(SQL, timeout=3))
        result.update(availability='available', code=None, items=rows[:20], truncated=len(rows) > 20,
                      observed_at=_now())
        # Match the shared HTTP responder, including its default ASCII escaping.
        if len(json.dumps(result, default=str).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except Exception:
        # No raw source exception, SQL, payload or partial records cross this boundary.
        result.update(availability='unavailable', code='RECENT_FAILURES_UNAVAILABLE', items=[], truncated=None,
                      observed_at=_now())
    if _authority(principal, role) != fingerprint:
        raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    return result
