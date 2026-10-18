"""Small calendar scheduler for the local jobs ledger; never catches up."""
from datetime import date, datetime, time as wall_time, timedelta, timezone
import calendar
import fcntl
import json
import logging
import re
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jobs_resident as jobs
import request_auth

_THREAD = None
_STOP = threading.Event()
_MUTEX = threading.Lock()


def _utc(now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('scheduler time must be a timezone-aware datetime')
    return now.astimezone(timezone.utc)


SCHEDULE_FIELDS = {'kind', 'every_minutes', 'time', 'timezone', 'date', 'weekdays', 'day_of_month'}


def _local_time(day, at, zone):
    hour, minute = map(int, at.split(':'))
    candidate = datetime.combine(day, wall_time(hour, minute), zone)
    utc = candidate.astimezone(timezone.utc)
    # fold=0 chooses the first occurrence; round-trip rejects nonexistent times.
    return utc if utc.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None) else None


def _validate_schedule(payload, now):
    kind = payload.get('kind')
    allowed = {
        'interval': {'every_minutes'}, 'daily': {'time', 'timezone'},
        'once': {'time', 'timezone', 'date'},
        'weekly': {'time', 'timezone', 'weekdays'},
        'monthly': {'time', 'timezone', 'day_of_month'},
    }
    if not isinstance(kind, str) or kind not in allowed:
        raise ValueError('schedule kind must be interval, daily, once, weekly or monthly')
    if (payload.keys() & SCHEDULE_FIELDS) - (allowed[kind] | {'kind'}):
        raise ValueError('schedule contains fields irrelevant to its kind')
    enabled = payload.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('enabled must be boolean')
    result = dict(kind=kind, every_minutes=None, time=None, timezone=None,
                  date=None, weekdays=[], day_of_month=None)
    if kind == 'interval':
        minutes = payload.get('every_minutes')
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ValueError('interval requires every_minutes between 1 and 10080')
        result['every_minutes'] = minutes
        return result
    at, zone = payload.get('time'), payload.get('timezone', 'Asia/Tokyo')
    if not isinstance(at, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
        raise ValueError('calendar schedule requires HH:MM time')
    if not isinstance(zone, str):
        raise ValueError('timezone must be an IANA name')
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('unknown IANA timezone') from None
    result.update(time=at, timezone=zone)
    if kind == 'once':
        day = payload.get('date')
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('once requires YYYY-MM-DD date')
        try:
            occurrence = _local_time(date.fromisoformat(day), at, tz)
        except (ValueError, OverflowError):
            raise ValueError('once requires a valid date') from None
        if occurrence is None:
            raise ValueError('once time does not exist in the selected timezone')
        if enabled and occurrence <= now:
            raise ValueError('enabled once schedule requires a future time')
        result['date'] = day
    elif kind == 'weekly':
        days = payload.get('weekdays')
        if not isinstance(days, list) or not days or any(type(day) is not int or not 1 <= day <= 7 for day in days) or len(set(days)) != len(days):
            raise ValueError('weekly requires unique ISO weekdays between 1 and 7')
        result['weekdays'] = sorted(days)
    elif kind == 'monthly':
        day = payload.get('day_of_month')
        if not (type(day) is int and 1 <= day <= 31) and day != 'last':
            raise ValueError("monthly requires day_of_month between 1 and 31 or 'last'")
        result['day_of_month'] = day
    return result


def _next(schedule, now, previous=None):
    if schedule['kind'] == 'interval':
        period = timedelta(minutes=schedule['every_minutes'])
        if previous is None:
            return now + period
        return previous + period * (int((now - previous) // period) + 1)
    zone = ZoneInfo(schedule['timezone'])
    if schedule['kind'] == 'once':
        occurrence = _local_time(date.fromisoformat(schedule['date']), schedule['time'], zone)
        return occurrence if occurrence is not None and occurrence > now else None
    day = now.astimezone(zone).date()
    for offset in range(370):
        candidate = day + timedelta(days=offset)
        if schedule['kind'] == 'weekly' and candidate.isoweekday() not in schedule['weekdays']:
            continue
        if schedule['kind'] == 'monthly':
            target = calendar.monthrange(candidate.year, candidate.month)[1] if schedule['day_of_month'] == 'last' else schedule['day_of_month']
            if candidate.day != target:
                continue
        utc = _local_time(candidate, schedule['time'], zone)
        if utc is not None and utc > now:
            return utc
    raise ValueError('calendar schedule has no valid future time')


def _persist(connection, name, actor, role, schedule, arguments, enabled, now):
    occurrence = _next(schedule, now) if enabled else None
    connection.execute("""INSERT INTO schedules
        (name,owner,kind,every_minutes,time,timezone,date,weekdays,day_of_month,arguments,enabled,role,auth_mode,next_run_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
        kind=excluded.kind,every_minutes=excluded.every_minutes,time=excluded.time,
        timezone=excluded.timezone,date=excluded.date,weekdays=excluded.weekdays,
        day_of_month=excluded.day_of_month,arguments=excluded.arguments,enabled=excluded.enabled,
        role=excluded.role,auth_mode=excluded.auth_mode,next_run_at=excluded.next_run_at""",
        (name, actor, schedule['kind'], schedule['every_minutes'], schedule['time'], schedule['timezone'],
         schedule['date'], json.dumps(schedule['weekdays']),
         str(schedule['day_of_month']) if schedule['day_of_month'] is not None else None,
         json.dumps(arguments), int(enabled), role, _mode(), occurrence.isoformat() if occurrence else None))


def _mode():
    return 'shared' if request_auth.shared_mode() else 'local'


def _authorize(schedule):
    if schedule['auth_mode'] != _mode():
        raise PermissionError('Authentication mode changed; schedule paused')
    jobs._check_account(schedule['owner'], schedule['role'], require_shared=schedule['auth_mode'] == 'shared')
    if schedule['auth_mode'] == 'local':
        jobs.wr.resolve_role(schedule['owner'], schedule['role'])


def _schedule(row, actor, connection):
    result = dict(row)
    result['arguments'] = json.loads(result['arguments'])
    result['weekdays'] = json.loads(result.get('weekdays') or '[]')
    day = result.get('day_of_month')
    result['day_of_month'] = int(day) if day is not None and day != 'last' else day
    result['enabled'] = bool(result['enabled'])
    result['can_manage'] = result['owner'] == actor
    if result['last_run_id']:
        last = connection.execute('SELECT status FROM runs WHERE id=?', (result['last_run_id'],)).fetchone()
        if last:
            result['last_status'] = last['status']
    return result


def save_schedule(payload, actor, role):
    jobs._identity(actor, role)
    jobs._check_account(actor, role)
    fields = SCHEDULE_FIELDS | {'name', 'arguments', 'enabled'}
    if not isinstance(payload, dict) or payload.keys() - fields:
        raise ValueError('invalid schedule fields')
    if payload.keys() == {'name', 'enabled'} and payload['enabled'] is False:
        if not isinstance(payload['name'], str):
            raise ValueError('schedule requires job name')
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT * FROM schedules WHERE name=?', (payload['name'],)).fetchone()
            if old is None:
                raise jobs.JobNotFound('Schedule not found')
            if old['owner'] != actor:
                raise PermissionError('Only the schedule owner can pause it')
            connection.execute('UPDATE schedules SET enabled=0,next_run_at=NULL WHERE name=?', (payload['name'],))
            return _schedule(connection.execute('SELECT * FROM schedules WHERE name=?', (payload['name'],)).fetchone(), actor, connection)
    if not isinstance(payload.get('name'), str) or not isinstance(payload.get('kind'), str):
        raise ValueError('schedule requires job name and kind')
    kind, enabled = payload['kind'], payload.get('enabled', False)
    if type(enabled) is not bool or not isinstance(payload.get('arguments', {}), dict):
        raise ValueError('enabled must be boolean and arguments must be an object')
    now = _utc()
    schedule = _validate_schedule(payload, now)
    schedule['name'] = payload['name']
    if _mode() == 'local':
        try:
            jobs.wr.resolve_role(actor, role)
        except ValueError as exc:
            raise PermissionError('Selected role exceeds the current local role ceiling') from exc
    with jobs._db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        job = connection.execute('SELECT * FROM jobs WHERE name=?', (schedule['name'],)).fetchone()
        if job is None:
            raise jobs.JobNotFound('Job not found')
        if job['owner'] != actor:
            raise PermissionError('Only the job owner can change its schedule')
        old = connection.execute('SELECT owner FROM schedules WHERE name=?', (schedule['name'],)).fetchone()
        if old and old['owner'] != actor:
            raise PermissionError('Only the schedule owner can change it')
        values, _ = jobs._validate(json.loads(job['definition']), role, payload.get('arguments', {}))
        _persist(connection, schedule['name'], actor, role, schedule, values, enabled, now)
        return _schedule(connection.execute('SELECT * FROM schedules WHERE name=?', (schedule['name'],)).fetchone(), actor, connection)


def list_schedules(actor, role):
    jobs._identity(actor, role)
    jobs._recover()
    with jobs._db() as connection:
        return [_schedule(row, actor, connection) for row in connection.execute(
            "SELECT * FROM schedules WHERE owner=? OR ?='admin' ORDER BY name", (actor, role)).fetchall()]


def _lock(filename):
    lock = (jobs._root() / filename).open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def tick(now=None):
    return _tick(_utc(now), refresh_clock=now is None)


def _tick(now, stop=None, refresh_clock=False):
    result = dict(started=[], skipped=[], paused=[], unknown=[])
    claim = _lock('jobs-scheduler-tick.lock')
    if claim is None:
        return result
    try:
        with jobs._db() as connection:
            names = [row['name'] for row in connection.execute(
                'SELECT name FROM schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,name', (now.isoformat(),))]
        for name in names:
            if stop is not None and stop.is_set():
                break
            active = jobs._lock()
            current = _utc() if refresh_clock else now
            try:
                prepared, run_id, paused = None, None, False
                with jobs._db() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    row = connection.execute('SELECT * FROM schedules WHERE name=? AND enabled=1', (name,)).fetchone()
                    if row is None or row['next_run_at'] is None:
                        continue
                    due = datetime.fromisoformat(row['next_run_at'])
                    if due > current:
                        continue
                    busy = active is None and connection.execute(
                        "SELECT 1 FROM runs WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None
                    if active is None and not busy:
                        continue  # UI recovery/admission holds the lock; defer this slot.
                    if active is not None:
                        jobs._lost(connection)
                    schedule = dict(row)
                    is_stage = name.startswith('stage:')
                    job = connection.execute('SELECT * FROM jobs WHERE name=?', (name,)).fetchone()
                    definition = json.loads(job['definition']) if job else dict(name=name, steps=[])
                    arguments = json.loads(row['arguments'])
                    reason = None
                    try:
                        _authorize(schedule)
                        if is_stage:
                            import stages_resident
                            definition, prepared = stages_resident._scheduled(name[6:], row['owner'], row['role'], connection=connection)
                        else:
                            if job is None or job['owner'] != row['owner']:
                                raise PermissionError('Job ownership unavailable; schedule paused')
                            arguments, prepared = jobs._validate(definition, row['role'], arguments)
                    except (ValueError, PermissionError, OSError) as exc:
                        paused, reason = True, 'Schedule validation failed (' + type(exc).__name__ + '); paused'
                    if reason is None and (current - due).total_seconds() >= 60:
                        reason = 'Scheduled time missed by at least 60 seconds; no catch-up'
                    if reason is None and busy:
                        reason = 'Another job is active; scheduled run skipped without a queue'
                    if reason is None:
                        try:
                            if is_stage:
                                definition, prepared = stages_resident._scheduled(name[6:], row['owner'], row['role'], preflight=True, connection=connection)
                            else:
                                arguments, prepared = jobs._validate(definition, row['role'], arguments, preflight=True)
                            _authorize(schedule)
                        except (ValueError, PermissionError, OSError) as exc:
                            paused, reason = True, 'Schedule input preflight failed (' + type(exc).__name__ + '); paused'
                    current = _utc() if refresh_clock else now
                    if due > current:
                        continue
                    if reason is None and (current - due).total_seconds() >= 60:
                        reason = 'Scheduled time missed by at least 60 seconds; no catch-up'
                    status = 'skipped' if reason else 'queued'
                    try:
                        schedule['weekdays'] = json.loads(schedule.get('weekdays') or '[]')
                        day = schedule.get('day_of_month')
                        schedule['day_of_month'] = int(day) if day is not None and day != 'last' else day
                        normalized = _validate_schedule({key: schedule[key] for key in SCHEDULE_FIELDS if schedule.get(key) is not None and not (key == 'weekdays' and not schedule[key])}, current)
                        occurrence = None if paused else _next(normalized, current, previous=due)
                    except (ValueError, TypeError, KeyError, OverflowError):
                        paused, status = True, 'skipped'
                        reason = 'Schedule calendar calculation failed; paused'
                        occurrence = None
                    next_run = occurrence.isoformat() if occurrence else None
                    steps = prepared if prepared is not None else definition.get('steps', [])
                    connection.execute('SAVEPOINT schedule_admission')
                    try:
                        run_id = jobs._insert_run(connection, definition, arguments, steps, row['owner'], row['role'],
                            trigger='schedule', scheduled_for=due.isoformat(), status=status, error=reason)
                    except jobs.JobConflict:
                        # Undo both the run and any earlier claims in this admission.
                        connection.execute('ROLLBACK TO schedule_admission')
                        status, reason = 'skipped', 'Scheduled run admission conflict; skipped without a queue'
                        run_id = jobs._insert_run(connection, definition, arguments, steps, row['owner'], row['role'],
                            trigger='schedule', scheduled_for=due.isoformat(), status=status, error=reason)
                    finally:
                        connection.execute('RELEASE schedule_admission')
                    connection.execute('UPDATE schedules SET enabled=?,next_run_at=?,last_status=?,last_run_id=?,last_scheduled_for=? WHERE name=?',
                        (0 if paused or schedule['kind'] == 'once' else 1, next_run, status, run_id, due.isoformat(), name))
                if paused:
                    result['paused'].append(name)
                if status == 'skipped':
                    result['skipped'].append(run_id)
                else:
                    try:
                        jobs._start_worker(run_id, prepared, row['owner'], row['role'], active,
                                           row['auth_mode'] == 'shared', name, auth_mode=row['auth_mode'])
                    except Exception:
                        # The slot is already consumed; mark the unstarted claim unknown.
                        with jobs._db() as connection:
                            jobs._lost(connection)
                        result['unknown'].append(run_id)
                    else:
                        active = None  # Worker owns and releases this descriptor.
                        result['started'].append(run_id)
            finally:
                if active is not None:
                    active.close()
    finally:
        claim.close()
    return result


def start_scheduler(interval=10):
    global _THREAD
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not 0 < interval <= 60:
        raise ValueError('scheduler poll interval must be between 0 and 60 seconds')
    with _MUTEX:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _STOP.clear()
        def run():
            while not _STOP.is_set():
                try:
                    _tick(_utc(), _STOP, refresh_clock=True)
                except Exception:
                    logging.getLogger(__name__).exception('scheduler tick failed')
                _STOP.wait(interval)
        _THREAD = threading.Thread(target=run, name='jobs-scheduler', daemon=True)
        _THREAD.start()
        return True


def stop_scheduler():
    with _MUTEX:
        _STOP.set()
        thread = _THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join()
