"""Explicit capture and bounded search of owned Docker severity metadata."""
from datetime import datetime, timezone
import fcntl
from http.client import HTTPException
import re
import time
from urllib.parse import urlencode

import yaml

import db_platform_telemetry_logs as logs
from loki import manage as loki

PATH = '/api/v1/bears/platform-loki-errors'
_LABELS = dict(service_name='shirokuma-local', component='sre-telemetry-severity')
_SELECTOR = '{service_name="shirokuma-local",component="sre-telemetry-severity"}'
FOUNDATION_SELECTOR = '{service_name="shirokuma-local",component="sre-foundation-severity",source="trino-opa"}'
_SOURCES = ('agenthouse', 'loki')
_LEVELS = ('error', 'fatal')
_LINE = 'telemetry-severity'
_SECOND = 10**9
_WINDOW = 600 * _SECOND
_SEARCH_LIMIT = 20
_DEDUP_LIMIT = 1000
_CONFIG_MAX_BYTES = 128 * 1024
_FAILURES = (OSError, ValueError, HTTPException, loki.LokiError, RecursionError, yaml.YAMLError)


def _call(check, path, payload=None, **options):
    check()
    try:
        return loki.request(path, payload, timeout=2, **options)
    finally:
        check()


def _window():
    end = time.time_ns()
    return end - _WINDOW, end


def _severity_sample(name, streams):
    _, _, events = logs._classified(name, streams)
    result = set()
    for (seconds, fraction), event in events:
        severity = event['severity']
        if severity in _LEVELS:
            stamp = datetime.fromisoformat(seconds.decode('ascii')).replace(tzinfo=timezone.utc)
            result.add((int(stamp.timestamp()) * _SECOND + int(fraction), name, severity))
    return dict(status='available', events=result)


def _retention(check, *, foundation=False):
    raw = _call(check, '/config', max_bytes=_CONFIG_MAX_BYTES)
    if not isinstance(raw, str) or len(raw.encode()) > _CONFIG_MAX_BYTES:
        return False
    config = yaml.safe_load(raw)
    if not isinstance(config, dict):
        return False
    compactor, limits = config.get('compactor'), config.get('limits_config')
    if not isinstance(compactor, dict) or not isinstance(limits, dict):
        return False
    rules = limits.get('retention_stream')
    if (compactor.get('retention_enabled') is not True
            or compactor.get('delete_request_store') != 'filesystem'
            or limits.get('deletion_mode') != 'disabled'
            or limits.get('retention_period', '0s') != '0s'
            or not isinstance(rules, list) or len(rules) not in (1, 2)):
        return False
    periods = ('1d', '24h', '24h0m0s')
    if len(rules) == 1:
        rule = rules[0]
        return (not foundation and isinstance(rule, dict) and rule.get('selector') == _SELECTOR
                and rule.get('period') in periods)
    selectors = set()
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {'selector', 'period', 'priority'}
                or rule['selector'] not in (_SELECTOR, FOUNDATION_SELECTOR)
                or rule['selector'] in selectors or type(rule['priority']) is not int
                or rule['priority'] != 1 or rule['period'] not in periods):
            return False
        selectors.add(rule['selector'])
    return selectors == {_SELECTOR, FOUNDATION_SELECTOR}


def _query(check, window, limit, *, selector=_SELECTOR, labels=_LABELS, sources=_SOURCES):
    start, end = window
    args = urlencode(dict(query=selector, start=start, end=end + 1, direction='backward', limit=limit))
    reply = _call(check, '/loki/api/v1/query_range?' + args)
    if not isinstance(reply, dict) or reply.get('status') != 'success':
        raise ValueError()
    data = reply.get('data')
    if not isinstance(data, dict) or data.get('resultType') != 'streams':
        raise ValueError()
    streams = data.get('result')
    if not isinstance(streams, list) or len(streams) > len(sources) * len(_LEVELS):
        raise ValueError()
    seen = set()
    events = []
    for stream in streams:
        if not isinstance(stream, dict) or set(stream) != {'stream', 'values'}:
            raise ValueError()
        actual, rows = stream['stream'], stream['values']
        if not isinstance(actual, dict):
            raise ValueError()
        source, severity = actual.get('source'), actual.get('severity')
        expected = dict(labels, source=source, severity=severity)
        # Loki can include its derived severity metadata in the stream response.
        if (source not in sources or severity not in _LEVELS
                or actual not in (expected, dict(expected, detected_level=severity))
                or (source, severity) in seen or not isinstance(rows, list) or not rows
                or len(events) + len(rows) > limit):
            raise ValueError()
        seen.add((source, severity))
        for row in rows:
            if (not isinstance(row, list) or len(row) != 2 or row[1] != _LINE
                    or not isinstance(row[0], str) or not re.fullmatch(r'[0-9]{19}', row[0])):
                raise ValueError()
            stamp = int(row[0])
            if not start <= stamp <= end:
                raise ValueError()
            events.append((stamp, source, severity))
    return events


def _utc(stamp):
    seconds, ns = divmod(stamp, _SECOND)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{ns:09d}Z'


def search(principal, role):
    check = logs._authority_check(principal, role)
    try:
        return _search(check, _window())
    finally:
        check()


def _search(check, window, *, foundation=False, **query_scope):
    result = dict(operation='search', status='unavailable', since_minutes=10,
                  window_end=_utc(window[1]), limit=_SEARCH_LIMIT, hits=[])
    try:
        if foundation and not _retention(check, foundation=True):
            return result
        events = _query(check, window, _SEARCH_LIMIT, **query_scope)
        result.update(status='available', hits=[
            dict(timestamp=_utc(stamp), source=source, severity=severity)
            for stamp, source, severity in sorted(set(events), reverse=True)
        ])
    except _FAILURES:
        pass
    return result


def _push_payload(events, labels=_LABELS):
    groups = {}
    for stamp, source, severity in sorted(events):
        groups.setdefault((source, severity), []).append([str(stamp), _LINE])
    return dict(streams=[
        dict(stream=dict(labels, source=source, severity=severity), values=values)
        for (source, severity), values in groups.items()
    ])


def capture(principal, role):
    check = logs._authority_check(principal, role)
    samples = logs._collect(check, _severity_sample, dict(status='unavailable', events=()))
    try:
        return _capture(check, samples, _window())
    finally:
        check()


def _capture(check, samples, window, *, foundation=False, **query_scope):
    events = {event for sample in samples.values() for event in sample['events']
              if window[0] <= event[0] <= window[1]}
    result = dict(operation='capture', status='unavailable', since_minutes=10,
                  window_end=_utc(window[1]), tail_lines=100, max_bytes=logs._MAX_BYTES, accepted=None,
                  sources={name: sample['status'] for name, sample in samples.items()})
    try:
        if not events:
            if ('available' in result['sources'].values()
                    and (not foundation or _retention(check, foundation=True))):
                result.update(status='no_new_events', accepted=0)
            return result

        # Serialize the reconciliation and push with the scoped Loki lifecycle.
        state = loki.ROOT / 'tmp/local-experiment/loki'
        with (state / 'runtime.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if foundation and not _retention(check, foundation=True):
                return result
            existing = _query(check, window, _DEDUP_LIMIT, **query_scope)
            if len(existing) == _DEDUP_LIMIT:
                return result
            pending = events.difference(existing)
            if not pending:
                result.update(status='no_new_events', accepted=0)
                return result
            if not foundation and not _retention(check):
                return result
            result['status'] = 'unconfirmed'
            payload = _push_payload(pending, query_scope.get('labels', _LABELS))
            if _call(check, '/loki/api/v1/push', payload, expected_status=204) == '':
                result.update(status='accepted', accepted=len(pending))
    except _FAILURES:
        pass
    return result
