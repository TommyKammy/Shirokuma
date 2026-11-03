"""Bounded read-only saved LOCAL summaries; never inspect process liveness."""
from datetime import datetime
import json
import re
import uuid

import audit_service as audit
from db_platform_observations import _authority, _now, SOURCE_ERRORS
from warehouse_operations import OperationError

ACTIONS = ('WAREHOUSE_WAKE', 'WAREHOUSE_CHILL', 'WAREHOUSE_AUTO_CHILL', 'WAREHOUSE_RECONCILE')
STATES = ('ACCEPTED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN', 'RECONCILED')
ERRORS = ('WAREHOUSE_BUSY', 'WAREHOUSE_STOP_FAILED', 'WAREHOUSE_OBSERVATION_FAILED',
          'WAREHOUSE_UNAVAILABLE', 'WAREHOUSE_RECOVERY_REQUIRED', 'OPERATION_FORBIDDEN',
          'RUNTIME_OPERATION_FAILED', 'POLICY_UNAVAILABLE', 'WAKE_FAILED', 'ADMISSION_CONFLICT',
          'ENGINE_ACCESS_DENIED', 'WAREHOUSE_WAKE_FAILED', 'WAKE_CANCELED',
          'FOUNDATION_NOT_READY', 'POLICY_WRITE_FAILED', 'WAREHOUSE_STOP_UNKNOWN',
          'OTHER_RUNTIME_ERROR')
COVERAGE = dict(kind='latest_saved_summary', ordering='insertion_order',
                full_transitions=False, liveness=False, current_probe=False)
MAX_ROW = 16384
MAX_DTO = 32768
MAX_ROWID = 9223372036854775807


def _json(value):
    if type(value) is not str or len(value.encode()) > MAX_ROW:
        raise ValueError()
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError()
            result[k] = v
        return result
    return json.loads(value, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _time(value):
    if value is None:
        return None
    if type(value) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00)', value):
        raise ValueError()
    datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.removesuffix('+00:00') + 'Z' if value.endswith('+00:00') else value


def _cursor(limit, fingerprint, ceiling, last):
    return audit._token(dict(purpose='platform_runtime_history', version=1, limit=limit,
                             authority=fingerprint, ceiling=ceiling, last=last))


def _anchor(cursor, limit, fingerprint):
    try:
        d = audit._untoken(cursor)
        if (type(d) is not dict or set(d) != {'purpose', 'version', 'limit', 'authority', 'ceiling', 'last'}
                or d['purpose'] != 'platform_runtime_history' or type(d['version']) is not int or d['version'] != 1
                or type(d['limit']) is not int or d['limit'] != limit or d['authority'] != fingerprint
                or type(d['ceiling']) is not int or not 0 <= d['ceiling'] < MAX_ROWID
                or type(d['last']) is not int or not 1 <= d['last'] <= d['ceiling'] + 1):
            raise ValueError()
        return d['ceiling'], d['last']
    except Exception:
        raise OperationError('STALE_CURSOR', 409) from None


def _origin(requests, row, kind):
    if len(requests) != 1:
        return 'unrecorded'
    r = requests[0]
    try:
        payload = _json(r['payload'])
        action = {'WAKE':'wake', 'CHILL':'chill', 'POLICY_SAVE':'auto-chill', 'RECONCILE':'reconcile'}.get(kind)
        body = payload['body']
        valid_body = body == {} if kind != 'POLICY_SAVE' else (type(body) is dict and set(body) == {'minutes','expected_revision'}
            and type(body['minutes']) is int and 0 <= body['minutes'] <= 1440
            and type(body['expected_revision']) is int and body['expected_revision'] >= 0)
        if (r['actor'] == row['actor'] and r['route'] == '/api/v1/warehouses/LOCAL/runtime'
                and r['state'] == 'REQUESTED' and type(payload) is dict and set(payload) == {'action','body'}
                and action is not None and payload['action'] == action and valid_body):
            return 'explicit_request'
    except (ValueError, KeyError, TypeError, RecursionError):
        pass
    return 'unrecorded'


def _project(requests, row):
    if str(uuid.UUID(row['id'])) != row['id'] or row['state'] not in STATES or _json(row['targets']) != ['LOCAL']:
        raise ValueError()
    meta, times = _json(row['before_after']), _json(row['timestamps'])
    if type(meta) is not dict or type(times) is not dict:
        raise ValueError()
    action = row['action']
    if 'policy_change' in meta and (type(meta['policy_change']) is not bool
            or meta['policy_change'] and action != 'WAREHOUSE_AUTO_CHILL'):
        raise ValueError()
    kind = 'POLICY_SAVE' if action == 'WAREHOUSE_AUTO_CHILL' and meta.get('policy_change') is True else action.removeprefix('WAREHOUSE_')
    error = meta.get('error_code')
    error = None if error is None else error if type(error) is str and error in ERRORS else 'OTHER_RUNTIME_ERROR'
    completed = times.get('succeeded') if row['state'] == 'SUCCEEDED' else times.get('failed') if row['state'] == 'FAILED' else times.get('succeeded', times.get('failed'))
    return dict(operation_id=row['id'], kind=kind, action=action, saved_state=row['state'],
        origin=_origin(requests, row, kind),
        stop_trigger={'CHILL': 'stop_request', 'AUTO_CHILL': 'auto_idle_request'}.get(kind, 'unknown'),
        accepted_at=_time(times.get('accepted')), started_at=_time(times.get('started', times.get('running'))),
        completed_at=_time(completed), reconciled_at=_time(times.get('reconciled')),
        error_category=error, dispatch_marker='recorded' if meta.get('physical_started') is True else 'not_recorded',
        reconciled_observed_state=meta.get('observed_state') if row['state'] == 'RECONCILED' and meta.get('observed_state') in ('active','chilled') else None)


def observe(principal, role, *, limit=20, cursor=None):
    fingerprint = _authority(principal, role)
    def fence():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    if type(limit) is not int or not 1 <= limit <= 50 or cursor is not None and (type(cursor) is not str or not 1 <= len(cursor) <= 2048):
        raise OperationError('INVALID_INPUT', 400)
    anchor = _anchor(cursor, limit, fingerprint) if cursor is not None else None
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(), coverage=dict(COVERAGE),
        availability='unavailable', code='HISTORY_UNAVAILABLE', items=None, page_cursor=None, next_cursor=None,
        scanned_count=0, omitted_count=0, limit=limit)
    try:
        fence()
        with audit.snapshot() as db:
            fence()
            ceiling = anchor[0] if anchor else db.execute('SELECT COALESCE(MAX(rowid),0) FROM operations').fetchone()[0]
            if type(ceiling) is not int or not 0 <= ceiling < MAX_ROWID:
                raise ValueError()
            last = anchor[1] if anchor else ceiling + 1
            fence()
            rows = db.execute('''SELECT rowid,id,actor,action,state,targets,
                CASE WHEN length(CAST(before_after AS BLOB))<=16384 THEN before_after ELSE NULL END AS before_after,
                CASE WHEN length(CAST(timestamps AS BLOB))<=16384 THEN timestamps ELSE NULL END AS timestamps FROM operations
                WHERE rowid<=? AND rowid<? AND action IN (?,?,?,?)
                AND CASE WHEN json_valid(targets) THEN json_type(targets)='array'
                  AND json_array_length(targets)=1 AND json_extract(targets,'$[0]')='LOCAL' ELSE 0 END
                ORDER BY rowid DESC LIMIT ?''',
                (ceiling, last, *ACTIONS, limit + 1)).fetchall()
            # One bounded same-page lookup; no join fanout and no per-row store scans.
            requests = {}
            if rows[:limit]:
                fence()
                ids = [row['id'] for row in rows[:limit]]
                saved = db.execute('''SELECT operation_id,actor,route,
                    CASE WHEN length(CAST(payload AS BLOB))<=16384 THEN payload ELSE NULL END AS payload,state
                    FROM warehouse_action_requests WHERE operation_id IN ('''+','.join('?' for _ in ids)+') LIMIT ?',
                    (*ids, 2*limit+1)).fetchall()
                # If malformed duplication exhausts this bound, no origin is confirmed.
                if len(saved) <= 2*limit:
                    for request in saved:
                        requests.setdefault(request['operation_id'], []).append(request)
            items, omitted = [], 0
            for row in rows[:limit]:
                try:
                    item = _project(requests.get(row['id'], []), row)
                    if len(json.dumps(items + [item]).encode()) > MAX_DTO - 4096:
                        raise ValueError()
                    items.append(item)
                except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                    omitted += 1
            result.update(availability='available', code=None, items=items,
                page_cursor=_cursor(limit, fingerprint, ceiling, last),
                next_cursor=_cursor(limit, fingerprint, ceiling, rows[limit-1]['rowid']) if len(rows) > limit else None,
                scanned_count=min(len(rows),limit), omitted_count=omitted)
        fence()
        if len(json.dumps(result).encode()) > MAX_DTO:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        result.update(availability='unavailable', code='HISTORY_UNAVAILABLE', items=None,
                      page_cursor=None, next_cursor=None, scanned_count=0, omitted_count=0)
    fence()
    return result
