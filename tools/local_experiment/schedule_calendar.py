"""Calendar schedule validation and occurrence calculation for Database schedules."""
from datetime import date, datetime, time as wall_time, timedelta, timezone
import calendar
import fcntl
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import control_store
import request_auth


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


def _mode():
    return 'shared' if request_auth.shared_mode() else 'local'


def _lock(filename):
    lock = (control_store.root() / filename).open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock
