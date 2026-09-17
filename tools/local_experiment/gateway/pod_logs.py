"""Fixed Trino TEXT header-shaped signals; raw logs never leave this module.

Counts describe matched text lines, not authenticated log events. Each collect
adds at most two application acquisition intents, without application retry.
The logs-only path has two 8s deadlines and owned-child cleanup allowances of
2s each. Existing context/Pod/usage operations have their own separate bounds.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import importlib.util
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode

if __package__:
    from . import pod_usage as U
else:  # Gateway loads fixed source files without changing global sys.path.
    _spec = importlib.util.spec_from_file_location('gateway_logs_usage', Path(__file__).with_name('pod_usage.py'))
    U = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(U)

SCOPE = 'fixed-trino-current-container-console'
MAX_BYTES = 32768
MAX_LINES = 4096
WINDOW_SECONDS = 300
MAX_FUTURE_NS = 5 * U.NS
MAX_COLLECTION_NS = 20 * U.NS
LIMITATIONS = ['unframed-text-not-event-counts', 'latest-file-window-incomplete',
               'name-time-correlation-only', 'no-negative-health-or-cause-claim']
REASONS = frozenset({'POD_LIST_INCOMPLETE', 'TARGET_NOT_FOUND', 'TARGET_AMBIGUOUS',
                     'POD_INVALID', 'POD_NOT_RUNNING', 'CONTAINER_UNSUPPORTED', 'IDENTITY_CHANGED',
                     'LOG_INVALID', 'FORMAT_UNSUPPORTED', 'TIME_INVALID',
                     'READ_LIMIT', 'READ_TIMEOUT', 'READ_FAILED'})
KEYS = frozenset({'schema_version', 'scope', 'state', 'reason', 'binding', 'identity',
                  'request_started_at', 'observed_at', 'since_time', 'get_attempts',
                  'fragment', 'limitations'})
FRAGMENT_KEYS = frozenset({'returned_bytes', 'complete_lines', 'header_lines',
                           'warn_header_lines', 'error_header_lines', 'unparsed_lines',
                           'first_prefix_at', 'last_prefix_at'})
APP_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}(?:Z|[+-][0-9]{4})\Z')
LEVELS = frozenset({'TRACE', 'DEBUG', 'INFO', 'WARN', 'ERROR', 'OFF'})
PodLogCleanupError = U.PodUsageCleanupError


class PodLogError(ValueError):
    def __init__(self, code='LOG_INVALID'):
        self.code = code if code in REASONS else 'LOG_INVALID'
        super().__init__(self.code)


def require(value, code='LOG_INVALID'):
    if not value:
        raise PodLogError(code)


def _stamp(value):
    try:
        return U.stamp(value)
    except U.PodUsageError:
        raise PodLogError('TIME_INVALID') from None


def since_time(request_started_at):
    """Use the same UTC whole-second filter forwarded by Kubernetes v1.33."""
    value, _ = _stamp(request_started_at)
    try:
        since = datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S') - timedelta(seconds=WINDOW_SECONDS)
        result = since.strftime('%Y-%m-%dT%H:%M:%SZ')
        _stamp(result)
        return result
    except (ValueError, OverflowError):
        raise PodLogError('TIME_INVALID') from None


def _identity(pod):
    try:
        identity, started = U.pod_identity(pod)
        created = U.timestamp_ns(pod['metadata']['creationTimestamp'])
        return identity, started, created
    except U.PodUsageError as error:
        raise PodLogError(error.code if error.code in REASONS else 'POD_INVALID') from None


def _identity_value(value):
    try:
        return U.validate_identity(value)
    except U.PodUsageError:
        raise PodLogError('POD_INVALID') from None


def validate(value):
    """Validate only closed safe fields; never reconstruct or retain log text."""
    require(isinstance(value, dict) and set(value) == KEYS)
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and
            value['scope'] == SCOPE and value['state'] in ('observed', 'unknown'))
    require(value['limitations'] == LIMITATIONS)
    require(type(value['get_attempts']) is int and 0 <= value['get_attempts'] <= 2)
    identity_started = _identity_value(value['identity']) if value['identity'] is not None else None
    clocks = [value[key] for key in ('request_started_at', 'observed_at', 'since_time')]
    require(all(x is None for x in clocks) or all(isinstance(x, str) for x in clocks), 'TIME_INVALID')
    start = end = since = None
    if clocks[0] is not None:
        start, end, since = [_stamp(x)[1] for x in clocks]
        require(start <= end and clocks[2] == since_time(clocks[0]), 'TIME_INVALID')
    if value['state'] == 'unknown':
        require(isinstance(value['reason'], str) and value['reason'] in REASONS and
                value['binding'] is None and value['fragment'] is None)
        return copy.deepcopy(value)
    require(value['reason'] is None and value['binding'] == 'name-time-correlated' and
            value['get_attempts'] == 2 and identity_started is not None and start is not None)
    require(start > identity_started + 999 and end - start <= MAX_COLLECTION_NS, 'TIME_INVALID')
    fragment = value['fragment']
    require(isinstance(fragment, dict) and set(fragment) == FRAGMENT_KEYS)
    require(type(fragment['returned_bytes']) is int and 0 <= fragment['returned_bytes'] < MAX_BYTES)
    counts = ['complete_lines', 'header_lines', 'warn_header_lines', 'error_header_lines', 'unparsed_lines']
    require(all(type(fragment[x]) is int and 0 <= fragment[x] <= MAX_LINES for x in counts))
    require(fragment['complete_lines'] == fragment['header_lines'] + fragment['unparsed_lines'] and
            fragment['warn_header_lines'] + fragment['error_header_lines'] <= fragment['header_lines'])
    if fragment['returned_bytes'] == 0:
        require(all(fragment[x] == 0 for x in counts) and fragment['first_prefix_at'] is None and
                fragment['last_prefix_at'] is None)
    else:
        require(1 <= fragment['header_lines'] <= fragment['complete_lines'] <= fragment['returned_bytes'])
        first, last = (_stamp(fragment[key])[1] for key in ('first_prefix_at', 'last_prefix_at'))
        require(max(since, identity_started + 1000) <= first <= last <= end + MAX_FUTURE_NS, 'TIME_INVALID')
    return copy.deepcopy(value)


def _header_level(text):
    # Extra annotation/baggage/message tabs are opaque; only the fixed prefix is
    # inspected. A forged header in a multiline message is still just text.
    fields = text.split('\t', 4)
    if len(fields) != 5 or fields[1] not in LEVELS or not fields[2] or not fields[3] or not APP_STAMP.fullmatch(fields[0]):
        return None
    try:
        datetime.strptime(fields[0], '%Y-%m-%dT%H:%M:%S.%f%z')
    except ValueError:
        return None
    return fields[1]


def _fragment(raw, *, since_ns, end_ns, started_ns):
    require(isinstance(raw, bytes))
    require(len(raw) < MAX_BYTES, 'READ_LIMIT')
    result = {'returned_bytes': len(raw), 'complete_lines': 0, 'header_lines': 0,
              'warn_header_lines': 0, 'error_header_lines': 0, 'unparsed_lines': 0,
              'first_prefix_at': None, 'last_prefix_at': None}
    if not raw:
        return result
    require(raw.endswith(b'\n') and not any(byte < 32 and byte not in (9, 10) or byte == 127 for byte in raw))
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        raise PodLogError() from None
    rows = text[:-1].split('\n')
    require(len(rows) <= MAX_LINES, 'READ_LIMIT')
    clocks = []
    for row in rows:
        stamp, separator, content = row.partition(' ')
        require(separator == ' ', 'LOG_INVALID')
        normalized, nanos = _stamp(stamp)
        require(max(since_ns, started_ns + 1000) <= nanos <= end_ns + MAX_FUTURE_NS, 'TIME_INVALID')
        clocks.append((nanos, normalized))
        level = _header_level(content)
        result['complete_lines'] += 1
        if level is None:
            result['unparsed_lines'] += 1
        else:
            result['header_lines'] += 1
            if level in ('WARN', 'ERROR'):
                result[level.lower() + '_header_lines'] += 1
    require(result['header_lines'] > 0, 'FORMAT_UNSUPPORTED')
    # Interleaved text need not be ordered. Prefix-shaped values can themselves
    # occur in an unescaped message: these are syntax bounds, not event times.
    result['first_prefix_at'] = min(clocks)[1]
    result['last_prefix_at'] = max(clocks)[1]
    return result


def project(before_pod, raw_bytes, after_pod, *, request_started_at, observed_at, since_time):
    identity, started_ns, created_ns = _identity(before_pod)
    other, other_started, other_created = _identity(after_pod)
    require((identity, started_ns, created_ns) == (other, other_started, other_created), 'IDENTITY_CHANGED')
    request, _ = _stamp(request_started_at)
    observed, end_ns = _stamp(observed_at)
    since, since_ns = _stamp(since_time)
    require(since_time == since[:19] + 'Z', 'TIME_INVALID')
    fragment = _fragment(raw_bytes, since_ns=since_ns, end_ns=end_ns,
                         started_ns=_identity_value(identity))
    return validate({'schema_version': 1, 'scope': SCOPE, 'state': 'observed', 'reason': None,
                     'binding': 'name-time-correlated', 'identity': identity,
                     'request_started_at': request, 'observed_at': observed,
                     'since_time': since[:19] + 'Z', 'get_attempts': 2, 'fragment': fragment,
                     'limitations': list(LIMITATIONS)})


def unknown(reason, *, identity=None, attempts=0, started=None, observed=None, since=None):
    return {'schema_version': 1, 'scope': SCOPE, 'state': 'unknown', 'reason': reason,
            'binding': None, 'identity': identity, 'request_started_at': started,
            'observed_at': observed, 'since_time': since, 'get_attempts': attempts,
            'fragment': None, 'limitations': list(LIMITATIONS)}


def collect(pod_list, *, limit, fetch, now):
    """A failed log acquisition never triggers readback; cancellation propagates."""
    identity = None
    attempts = 0
    started = observed = since = None
    try:
        require(isinstance(pod_list, dict) and isinstance(pod_list.get('items'), list), 'POD_LIST_INCOMPLETE')
        items = pod_list['items']
        metadata = pod_list.get('metadata', {})
        require(isinstance(metadata, dict) and type(limit) is int and 1 <= limit <= 50 and
                len(items) <= limit and not metadata.get('continue'), 'POD_LIST_INCOMPLETE')
        matches = [pod for pod in items if isinstance(pod, dict) and
                   isinstance(pod.get('metadata'), dict) and isinstance(pod['metadata'].get('labels'), dict) and
                   pod['metadata']['labels'].get(U.LABEL) == U.TARGET]
        require(bool(matches), 'TARGET_NOT_FOUND')
        require(len(matches) == 1, 'TARGET_AMBIGUOUS')
        before = matches[0]
        identity, _, _ = _identity(before)
        candidate = _stamp(now())[0]
        since = since_time(candidate)
        started = candidate
        attempts = 1
        raw = fetch('logs', identity['pod_name'], since_time=since)
        # Raw returned successfully; bind it to a fresh read of the same Pod.
        attempts = 2
        after = fetch('pod', identity['pod_name'])
        observed = _stamp(now())[0]
        return project(before, raw, after, request_started_at=started, observed_at=observed, since_time=since)
    except PodLogCleanupError:
        raise
    except Exception as error:
        reason = error.code if isinstance(error, PodLogError) else 'READ_FAILED'
        if started is not None and observed is None:
            try:
                observed = _stamp(now())[0]
            except Exception:
                started = observed = since = None
        if started is not None and _stamp(started)[1] > _stamp(observed)[1]:
            started = observed = since = None
            reason = 'TIME_INVALID'
        return validate(unknown(reason, identity=identity, attempts=attempts,
                                started=started, observed=observed, since=since))


def fixed_get(operation, pod_name, *, since_time=None):
    """Only this container's bounded non-follow logs or its exact Pod JSON."""
    require(operation in ('logs', 'pod') and U.name(pod_name), 'READ_FAILED')
    if operation == 'pod':
        require(since_time is None, 'READ_FAILED')
        try:
            return U.fixed_get('pod', pod_name)
        except U.PodUsageError as error:
            raise PodLogError(error.code) from None
    normalized, _ = _stamp(since_time)
    require(since_time == normalized[:19] + 'Z', 'TIME_INVALID')
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL')
                   if key in os.environ}
    deadline = time.monotonic() + U.READ_SECONDS
    try:
        server = U._local_server(deadline=deadline, environment=environment)
        query = urlencode({'container': U.CONTAINER, 'follow': 'false', 'previous': 'false',
                           'timestamps': 'true', 'sinceTime': since_time, 'limitBytes': str(MAX_BYTES)})
        path = f'/api/v1/namespaces/{U.NAMESPACE}/pods/{pod_name}/log?{query}'
        raw = U._command_bytes(['--server', server, '--request-timeout=6s', 'get', '--raw', path],
                               deadline=deadline, environment=environment, max_bytes=MAX_BYTES)
        require(len(raw) < MAX_BYTES, 'READ_LIMIT')
        return raw
    except U.PodUsageError as error:
        raise PodLogError(error.code) from None
