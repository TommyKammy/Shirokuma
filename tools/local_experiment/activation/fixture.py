"""One new fixed Pawprint fixture; the caller persists intent before append."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
import uuid

from tools.local_experiment.agenthouse import manage as A
from tools.local_experiment.query_history import import_receipts as H

AGENT = 'local-activation-fixture'
ACTION = 'activation_fixture'
TARGET = 'grafana'
TASK = re.compile(r'activation-[0-9a-f]{32}')
SETTINGS = (
    " SETTINGS max_execution_time=10, max_threads=1, max_result_rows=2,"
    " max_result_bytes=16384, result_overflow_mode='throw',"
    " output_format_json_quote_64bit_integers=0, date_time_output_format='simple'"
    " FORMAT JSONEachRow"
)


class FixtureError(RuntimeError):
    pass


def require(condition, code='ACTIVATION_FIXTURE_INVALID'):
    if not condition:
        raise FixtureError(code)


def build(task_id, status):
    """Return a fresh event for an intent file; perform no runtime operation."""
    require(type(task_id) is str and TASK.fullmatch(task_id) is not None)
    require(type(status) is str and status in ('failed', 'success'))
    return A.event(ACTION, TARGET, status, agent_id=AGENT, task_id=task_id)


def _validate(value):
    require(type(value) is dict and set(value) == A.EVENT_FIELDS)
    require(type(value['task_id']) is str and TASK.fullmatch(value['task_id']) is not None)
    require(type(value['status']) is str and value['status'] in ('failed', 'success'))
    try:
        identity = uuid.UUID(value['event_id'])
        require(type(value['event_id']) is str and str(identity) == value['event_id']
                and identity.version == 4)
        stamp = datetime.strptime(value['timestamp'], '%Y-%m-%d %H:%M:%S.%f')
        require(stamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] == value['timestamp'])
        expected = A.event(ACTION, TARGET, value['status'], agent_id=AGENT,
                           task_id=value['task_id'], event_id=value['event_id'],
                           timestamp=value['timestamp'])
        require(all(type(value[key]) is type(expected[key]) and value[key] == expected[key]
                    for key in expected))
    except (TypeError, ValueError, AttributeError, A.AgentHouseError):
        raise FixtureError('ACTIVATION_FIXTURE_INVALID') from None
    return deepcopy(value)


def _rows(client, identity):
    # UUID is canonical and validated before interpolation. No caller SQL exists.
    sql = ('SELECT ' + ','.join(sorted(A.EVENT_FIELDS))
           + " FROM agenthouse.pawprints WHERE event_id='" + identity + "' LIMIT 2" + SETTINGS)
    raw = client.query(sql)
    require(type(raw) is str and len(raw.encode('utf-8')) <= 16384,
            'ACTIVATION_FIXTURE_READBACK_INVALID')
    try:
        rows = H.database_rows(raw)
        require(len(rows) <= 2 and all(type(row) is dict for row in rows),
                'ACTIVATION_FIXTURE_READBACK_INVALID')
        # ClickHouse may quote UInt64 in JSON. Input events still require int.
        for row in rows:
            if type(row.get('duration_ms')) is str and row['duration_ms'] == '0':
                row['duration_ms'] = 0
        return rows
    except H.ImportFailure:
        raise FixtureError('ACTIVATION_FIXTURE_READBACK_INVALID') from None


def _first(original, later):
    if original is None or (isinstance(original, Exception) and not isinstance(later, Exception)):
        return later
    return original


def append(event, *, store=None):
    """Insert once and return exact verified data; never create storage or retry.

    The caller must save the built event and an exclusive write intent first.
    An exception after INSERT may mean a row exists: retain the intent without
    retrying, even when the transport reply or final scope verification failed.
    """
    expected = _validate(event)
    store = H.runtime_store() if store is None else store
    first = None
    try:
        store.guard()
        require(_rows(store.client, expected['event_id']) == [],
                'ACTIVATION_FIXTURE_EVENT_EXISTS')
        store.guard()
        A.insert(store.client, expected)
        rows = _rows(store.client, expected['event_id'])
        require(len(rows) == 1, 'ACTIVATION_FIXTURE_READBACK_INVALID')
        require(_validate(rows[0]) == expected, 'ACTIVATION_FIXTURE_READBACK_INVALID')
    except BaseException as error:
        first = error
    finally:
        try:
            store.guard()
        except BaseException as error:
            first = _first(first, error)
    if first is not None:
        raise first
    return deepcopy(expected)
