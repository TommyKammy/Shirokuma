"""Pure, bounded application-container facts; no messages or cause inference.

Every output has exactly status/scope/containers. Names are sorted; at most eight
application-container rows are retained. The producer compares all application
spec names with all status names before declaring completeness. Invalid or absent
data takes precedence over truncation. Init and ephemeral containers are excluded.

Rows contain name, ready (bool/null), restarts (nonnegative int32/null), state,
and last_termination. States have exactly kind/reason/exit_code/started_at/
finished_at. Running fields other than started_at are null; waiting fields other
than reason are null. Unknown kind fixes all four other fields to null. A known
state can have an unknown reason, but then the observation status must be unknown.
Optional unreported reasons and timestamps remain null. Invalid mandatory ready,
restart or terminated exit fields become null and make the observation unknown.
Exit codes are int32, never booleans. Times are UTC with six fractional digits.

Empty/absent lastState means no prior termination was reported; it is not copied
from current state. A malformed/non-terminated lastState makes status unknown.
These fields establish reported states only, not root cause or process identity.
Legacy snapshots without container_observation remain outside this new contract;
consumers must label that boundary not-observed rather than synthesize completeness.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

MAX_CONTAINERS = 8
MAX_INPUT_CONTAINERS = 256
WAITING_REASONS = frozenset({
    'CrashLoopBackOff', 'ImagePullBackOff', 'ErrImagePull', 'ContainerCreating',
    'PodInitializing', 'CreateContainerConfigError', 'CreateContainerError', 'RunContainerError',
})
TERMINATED_REASONS = frozenset({'Completed', 'Error', 'OOMKilled', 'ContainerCannotRun'})
NAME = re.compile(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?')
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z')


class ContainerObservationError(ValueError):
    pass


def _integer(value, *, minimum=0):
    return value if type(value) is int and minimum <= value <= 2147483647 else None


def _unknown_state():
    return {'kind': 'unknown', 'reason': None, 'exit_code': None,
            'started_at': None, 'finished_at': None}


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ContainerObservationError('invalid container observation fields')


def _validate_state(value, *, previous=False):
    _keys(value, ('kind', 'reason', 'exit_code', 'started_at', 'finished_at'))
    kind = value['kind']
    if type(kind) is not str or kind not in {'running', 'waiting', 'terminated', 'unknown'}:
        raise ContainerObservationError('invalid reported container state')
    if previous and kind != 'terminated':
        raise ContainerObservationError('prior state must be a reported termination')
    if kind == 'unknown':
        if any(value[field] is not None for field in ('reason', 'exit_code', 'started_at', 'finished_at')):
            raise ContainerObservationError('unknown state cannot invent reported fields')
        return False
    allowed = WAITING_REASONS if kind == 'waiting' else TERMINATED_REASONS if kind == 'terminated' else frozenset()
    reason = value['reason']
    if reason is not None and (type(reason) is not str or reason not in allowed | {'unknown'}):
        raise ContainerObservationError('invalid reported container reason')
    exit_code = value['exit_code']
    if exit_code is not None and _integer(exit_code, minimum=-2147483648) is None:
        raise ContainerObservationError('invalid reported exit code')
    null_fields = (('reason', 'exit_code', 'finished_at') if kind == 'running' else
                   ('exit_code', 'started_at', 'finished_at') if kind == 'waiting' else ())
    if any(value[field] is not None for field in null_fields):
        raise ContainerObservationError('state contains fields from another state kind')
    for field in ('started_at', 'finished_at'):
        stamp = value[field]
        if stamp is not None:
            try:
                if type(stamp) is not str or not STAMP.fullmatch(stamp):
                    raise ValueError()
                datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            except ValueError:
                raise ContainerObservationError('invalid reported state timestamp') from None
    ordered = (value['started_at'] is None or value['finished_at'] is None
               or value['started_at'] <= value['finished_at'])
    return reason != 'unknown' and (kind != 'terminated' or exit_code is not None) and ordered


def validate_container_observation(value):
    """Reject raw/extra fields and false complete claims without performing I/O."""
    _keys(value, ('status', 'scope', 'containers'))
    if value['scope'] != 'application-containers' or value['status'] not in ('complete', 'truncated', 'unknown'):
        raise ContainerObservationError('invalid container observation scope or completeness')
    rows = value['containers']
    if type(rows) is not list or len(rows) > MAX_CONTAINERS:
        raise ContainerObservationError('container observation exceeds its bound')
    names, complete = [], bool(rows)
    for row in rows:
        _keys(row, ('name', 'ready', 'restarts', 'state', 'last_termination'))
        name = row['name']
        if type(name) is not str or not NAME.fullmatch(name):
            raise ContainerObservationError('invalid container name')
        names.append(name)
        if row['ready'] is not None and type(row['ready']) is not bool:
            raise ContainerObservationError('invalid reported ready state')
        if row['restarts'] is not None and _integer(row['restarts']) is None:
            raise ContainerObservationError('invalid reported restart count')
        state_valid = _validate_state(row['state'])
        complete = complete and state_valid and row['ready'] is not None and row['restarts'] is not None
        if row['ready'] is True and row['state']['kind'] != 'running':
            complete = False
        last = row['last_termination']
        if last is not None:
            prior_valid = _validate_state(last, previous=True)
            complete = complete and prior_valid
            if (last['finished_at'] is not None and row['state']['started_at'] is not None
                    and last['finished_at'] > row['state']['started_at']):
                complete = False
    if names != sorted(set(names)):
        raise ContainerObservationError('container names are duplicated or unordered')
    if value['status'] != 'unknown' and not complete:
        raise ContainerObservationError('incomplete container fields cannot claim completeness')
    if value['status'] == 'truncated' and len(rows) != MAX_CONTAINERS:
        raise ContainerObservationError('truncation requires a full bounded projection')
    return value


def _state(value, timestamp: Callable, *, previous=False):
    if type(value) is not dict or len(value) != 1:
        return _unknown_state(), False
    kind, raw = next(iter(value.items()))
    if kind not in {'running', 'waiting', 'terminated'} or type(raw) is not dict or not raw:
        return _unknown_state(), False
    if previous and kind != 'terminated':
        return _unknown_state(), False
    result = {**_unknown_state(), 'kind': kind}
    valid = True
    fields = ({'startedAt'} if kind == 'running' else {'reason', 'message'} if kind == 'waiting' else
              {'exitCode', 'signal', 'reason', 'message', 'startedAt', 'finishedAt', 'containerID'})
    if set(raw) - fields:
        valid = False
    if kind != 'running':
        reason = raw.get('reason')
        allowed = WAITING_REASONS if kind == 'waiting' else TERMINATED_REASONS
        if reason in (None, ''):
            result['reason'] = None
        elif type(reason) is str and reason in allowed:
            result['reason'] = reason
        else:
            result['reason'] = 'unknown'
            valid = False
    if kind == 'terminated':
        result['exit_code'] = _integer(raw.get('exitCode'), minimum=-2147483648)
        valid = valid and result['exit_code'] is not None
        if 'signal' in raw and _integer(raw['signal']) is None:
            valid = False
    for source, target in (('startedAt', 'started_at'), ('finishedAt', 'finished_at')):
        if source not in fields:
            continue
        raw_time = raw.get(source)
        result[target] = timestamp(raw_time)
        if raw_time is not None and result[target] is None:
            valid = False
    if (result['started_at'] is not None and result['finished_at'] is not None
            and result['finished_at'] < result['started_at']):
        valid = False
    return result, valid


def _by_name(values):
    if type(values) is not list or not values or len(values) > MAX_INPUT_CONTAINERS:
        return None
    result = {}
    for row in values:
        if type(row) is not dict:
            return None
        name = row.get('name')
        if type(name) is not str or not NAME.fullmatch(name) or name in result:
            return None
        result[name] = row
    return result


def project_container_observation(pod, *, timestamp: Callable):
    """Project only application states using the caller's fixed UTC time parser.

    Absence is unknown, not an empty successful observation. ``complete`` means
    every expected application container has a structurally usable status; it
    does not establish readiness, root cause, or init/ephemeral-container health.
    Optional unreported fields stay null. Unknown reason text is never retained.
    """
    result = {'status': 'unknown', 'scope': 'application-containers', 'containers': []}
    if type(pod) is not dict or type(pod.get('spec')) is not dict or type(pod.get('status')) is not dict:
        return result
    expected = _by_name(pod['spec'].get('containers'))
    observed = _by_name(pod['status'].get('containerStatuses'))
    if expected is None or observed is None or set(expected) != set(observed):
        return result
    valid = True
    projected = []
    for name in sorted(observed):
        raw = observed[name]
        state, state_valid = _state(raw.get('state'), timestamp)
        ready = raw.get('ready') if type(raw.get('ready')) is bool else None
        restarts = _integer(raw.get('restartCount'))
        valid = valid and state_valid and ready is not None and restarts is not None
        if ready is True and state['kind'] != 'running':
            valid = False
        last = raw.get('lastState', {})
        last_termination = None
        if last != {}:
            previous, previous_valid = _state(last, timestamp, previous=True)
            if previous['kind'] == 'terminated':
                last_termination = previous
            valid = valid and previous_valid
            if (last_termination is not None and last_termination['finished_at'] is not None
                    and state['started_at'] is not None and last_termination['finished_at'] > state['started_at']):
                valid = False
        projected.append({'name': name, 'ready': ready, 'restarts': restarts,
                          'state': state, 'last_termination': last_termination})
    result['containers'] = projected[:MAX_CONTAINERS]
    result['status'] = ('unknown' if not valid else
                        'truncated' if len(projected) > MAX_CONTAINERS else 'complete')
    return validate_container_observation(result)
