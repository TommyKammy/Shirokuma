"""One supplementary, name/time-correlated Trino resource sample; never a cause.

PodMetrics has no container ID and metrics-server does not copy the Pod UID.
The before/after Pod identity therefore bounds correlation, not provenance of
the cached sample. CPU is a window average (a startup baseline may be synthetic);
memory is the last working set, not a peak. Metrics creationTimestamp is unused.
get_attempts counts at most two kubectl acquisition intents, without application
retries. Each config+acquisition has an eight-second deadline and 64 KiB stdout
cap (plus two seconds to reap an owned child). client-go can retry HTTP within
that deadline, so this is not a wire-level GET count guarantee.
Sources: kubernetes/metrics v0.33.4 pkg/apis/metrics/v1beta1/types.go and
kubernetes-sigs/metrics-server v0.8.0 pkg/storage/{pod,types}.go.
HTTP retry source: https://raw.githubusercontent.com/kubernetes/client-go/v0.33.4/rest/request.go
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
LABEL = 'app.kubernetes.io/name'
TARGET = 'trino-local'
CONTAINER = 'trino'
MAX_BYTES = 64 * 1024
READ_SECONDS = 8
CLEANUP_SECONDS = 2
NS = 10**9
INT64 = 2**63 - 1
MAX_AGE_NS = 120 * NS
MAX_FUTURE_NS = 5 * NS
REASONS = frozenset({
    'POD_LIST_INCOMPLETE', 'TARGET_NOT_FOUND', 'TARGET_AMBIGUOUS',
    'POD_INVALID', 'POD_NOT_RUNNING', 'CONTAINER_UNSUPPORTED', 'IDENTITY_CHANGED',
    'METRICS_INVALID', 'QUANTITY_UNSUPPORTED', 'TIME_INVALID', 'SAMPLE_STALE',
    'SAMPLE_PRECEDES_CONTAINER', 'READ_LIMIT', 'READ_TIMEOUT', 'READ_FAILED',
})
KEYS = frozenset({'schema_version', 'scope', 'state', 'reason', 'binding',
                  'request_started_at', 'observed_at', 'identity', 'sample', 'get_attempts'})
IDENTITY_KEYS = frozenset({'namespace', 'pod_name', 'pod_uid', 'container',
                           'container_id_sha256', 'restart_count', 'started_at'})
SAMPLE_KEYS = frozenset({'timestamp', 'window_ns', 'cpu_nanocores', 'memory_working_set_bytes'})


class PodUsageError(ValueError):
    def __init__(self, code='METRICS_INVALID'):
        self.code = code if code in REASONS else 'METRICS_INVALID'
        super().__init__(self.code)


class PodUsageCleanupError(RuntimeError):
    """Owned transport termination was not verified; never a usable observation."""
    def __init__(self):
        super().__init__('POD_USAGE_CLEANUP_FAILED')


def require(condition, code='METRICS_INVALID'):
    if not condition:
        raise PodUsageError(code)


def stamp(value):
    """Exact UTC nanoseconds; source precision is never parsed through float."""
    require(isinstance(value, str), 'TIME_INVALID')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z', value)
    require(match is not None, 'TIME_INVALID')
    try:
        clock = datetime.strptime(match[1], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        delta = clock - datetime(1970, 1, 1, tzinfo=timezone.utc)
        require(delta.days >= 0, 'TIME_INVALID')
        nanos = (delta.days * 86400 + delta.seconds) * NS + int((match[2] or '').ljust(9, '0'))
        return match[1] + '.' + (match[2] or '').ljust(9, '0') + 'Z', nanos
    except (ValueError, OverflowError):
        raise PodUsageError('TIME_INVALID') from None


def name(value):
    return (isinstance(value, str) and len(value) <= 253 and
            re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value) is not None and
            all(part and len(part) <= 63 and not part.startswith('-') and not part.endswith('-')
                for part in value.split('.')))


def timestamp_ns(value):
    return stamp(value)[1]


def uint(value, *, positive=False):
    require(isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]{0,18}', value) is not None)
    number = int(value)
    require(int(positive) <= number <= INT64)
    return number


def quantity(value, resource):
    """Exact supported Kubernetes quantities; unsupported syntax stays unknown.

    apimachinery v0.33.4 resource/suffix.go uses k, not K. Fractional bytes or
    nanocores, exponent forms and overflow are intentionally unsupported.
    """
    require(isinstance(value, str) and len(value) <= 64, 'QUANTITY_UNSUPPORTED')
    match = re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?([A-Za-z]*)', value)
    require(match is not None, 'QUANTITY_UNSUPPORTED')
    suffix = match[1]
    if resource == 'cpu':
        factors = {'n': 1, 'u': 1000, 'm': 10**6, '': NS}
    elif resource == 'memory':
        factors = {'': 1, 'k': 1000, 'M': 10**6, 'G': 10**9, 'T': 10**12,
                   'P': 10**15, 'E': 10**18, **{x + 'i': 1024**n for n, x in enumerate('KMGTPE', 1)}}
    else:
        raise PodUsageError('QUANTITY_UNSUPPORTED')
    require(suffix in factors, 'QUANTITY_UNSUPPORTED')
    numeric = value[:-len(suffix)] if suffix else value
    number = Fraction(numeric) * factors[suffix]
    require(number.denominator == 1 and 0 <= number.numerator <= INT64, 'QUANTITY_UNSUPPORTED')
    return str(number.numerator)


def pod_identity(pod):
    require(isinstance(pod, dict), 'POD_INVALID')
    metadata, spec, status = (pod.get(key) for key in ('metadata', 'spec', 'status'))
    require(all(isinstance(x, dict) for x in (metadata, spec, status)), 'POD_INVALID')
    require(metadata.get('namespace') == NAMESPACE and name(metadata.get('name')) and
            isinstance(metadata.get('labels'), dict) and metadata['labels'].get(LABEL) == TARGET and
            metadata.get('deletionTimestamp') is None, 'POD_INVALID')
    uid = metadata.get('uid')
    try:
        require(isinstance(uid, str) and str(uuid.UUID(uid)) == uid, 'POD_INVALID')
    except (ValueError, AttributeError):
        raise PodUsageError('POD_INVALID') from None
    require(status.get('phase') == 'Running', 'POD_NOT_RUNNING')
    containers, states = spec.get('containers'), status.get('containerStatuses')
    require(isinstance(containers, list) and len(containers) == 1 and
            isinstance(containers[0], dict) and containers[0].get('name') == CONTAINER and
            isinstance(states, list) and len(states) == 1 and isinstance(states[0], dict) and
            states[0].get('name') == CONTAINER and not spec.get('ephemeralContainers') and
            not spec.get('initContainers'), 'CONTAINER_UNSUPPORTED')
    state = states[0]
    require(type(state.get('restartCount')) is int and 0 <= state['restartCount'] <= 2**31 - 1,
            'POD_INVALID')
    require(isinstance(state.get('state'), dict) and set(state['state']) == {'running'} and
            isinstance(state['state']['running'], dict), 'POD_NOT_RUNNING')
    started, raw_started_ns = stamp(state['state']['running'].get('startedAt'))
    _, created_ns = stamp(metadata.get('creationTimestamp'))
    require(created_ns <= raw_started_ns, 'POD_INVALID')
    container_id = state.get('containerID')
    require(isinstance(container_id, str) and re.fullmatch(r'docker://[a-f0-9]{64}', container_id),
            'CONTAINER_UNSUPPORTED')
    return {'namespace': NAMESPACE, 'pod_name': metadata['name'], 'pod_uid': uid,
            'container': CONTAINER, 'container_id_sha256': hashlib.sha256(container_id.encode()).hexdigest(),
            'restart_count': state['restartCount'], 'started_at': started[:26] + 'Z'}, raw_started_ns


def validate_identity(value):
    require(isinstance(value, dict) and set(value) == IDENTITY_KEYS)
    require(value['namespace'] == NAMESPACE and value['container'] == CONTAINER and name(value['pod_name']))
    try:
        require(isinstance(value['pod_uid'], str) and str(uuid.UUID(value['pod_uid'])) == value['pod_uid'])
    except (ValueError, AttributeError):
        raise PodUsageError() from None
    require(isinstance(value['container_id_sha256'], str) and
            re.fullmatch('[0-9a-f]{64}', value['container_id_sha256']) is not None)
    require(type(value['restart_count']) is int and 0 <= value['restart_count'] <= 2**31 - 1)
    normalized, nanos = stamp(value['started_at'])
    require(value['started_at'] == normalized[:26] + 'Z')
    return nanos


def validate(value):
    """Validate only the closed public projection; absent legacy fields stay absent."""
    require(isinstance(value, dict) and set(value) == KEYS)
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and
            value['scope'] == 'fixed-trino-container' and value['state'] in ('observed', 'unknown'))
    require(type(value['get_attempts']) is int and 0 <= value['get_attempts'] <= 2)
    started = validate_identity(value['identity']) if value['identity'] is not None else None
    times = []
    for key in ('request_started_at', 'observed_at'):
        times.append(stamp(value[key])[1] if value[key] is not None else None)
    require((times[0] is None) == (times[1] is None))
    if times[0] is not None:
        require(times[0] <= times[1], 'TIME_INVALID')
    if value['state'] == 'unknown':
        require(isinstance(value['reason'], str) and value['reason'] in REASONS and
                value['sample'] is None and value['binding'] is None)
    else:
        require(value['reason'] is None and value['binding'] == 'name-time-correlated' and
                value['get_attempts'] == 2 and started is not None and times[0] is not None)
        sample = value['sample']
        require(isinstance(sample, dict) and set(sample) == SAMPLE_KEYS)
        sample_ns = stamp(sample['timestamp'])[1]
        window = uint(sample['window_ns'], positive=True)
        uint(sample['cpu_nanocores']); uint(sample['memory_working_set_bytes'])
        require(window <= MAX_AGE_NS and sample_ns - window > started + 999, 'SAMPLE_PRECEDES_CONTAINER')
        require(-MAX_FUTURE_NS <= times[1] - sample_ns <= MAX_AGE_NS, 'SAMPLE_STALE')
        require(times[1] - times[0] <= 2 * (READ_SECONDS + CLEANUP_SECONDS) * NS, 'TIME_INVALID')
    return copy.deepcopy(value)


def unknown(reason, *, identity=None, attempts=0, started=None, observed=None):
    return {'schema_version': 1, 'scope': 'fixed-trino-container', 'state': 'unknown',
            'reason': reason, 'binding': None, 'request_started_at': started, 'observed_at': observed,
            'identity': identity, 'sample': None, 'get_attempts': attempts}


def project(before, metrics, after, *, request_started_at, observed_at):
    """Metrics request start through after-Pod completion, on the same UTC clock."""
    identity, began = pod_identity(before)
    other, ended = pod_identity(after)
    require(identity == other and began == ended and
            stamp(before['metadata']['creationTimestamp'])[1] ==
            stamp(after['metadata']['creationTimestamp'])[1], 'IDENTITY_CHANGED')
    require(isinstance(metrics, dict) and metrics.get('kind') == 'PodMetrics' and
            metrics.get('apiVersion') == 'metrics.k8s.io/v1beta1', 'METRICS_INVALID')
    metadata = metrics.get('metadata')
    require(isinstance(metadata, dict) and metadata.get('name') == identity['pod_name'] and
            metadata.get('namespace') == NAMESPACE and metadata.get('uid', identity['pod_uid']) == identity['pod_uid'])
    containers = metrics.get('containers')
    require(isinstance(containers, list) and len(containers) == 1 and isinstance(containers[0], dict) and
            containers[0].get('name') == CONTAINER and isinstance(containers[0].get('usage'), dict))
    usage = containers[0]['usage']
    window = metrics.get('window')
    require(isinstance(window, str) and len(window) <= 32 and
            re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?s', window) is not None, 'TIME_INVALID')
    nanos = Fraction(window[:-1]) * NS
    require(nanos.denominator == 1 and 0 < nanos <= MAX_AGE_NS, 'TIME_INVALID')
    timestamp, sample_ns = stamp(metrics.get('timestamp'))
    require(sample_ns - int(nanos) > began, 'SAMPLE_PRECEDES_CONTAINER')
    result = {'schema_version': 1, 'scope': 'fixed-trino-container', 'state': 'observed',
              'reason': None, 'binding': 'name-time-correlated',
              'request_started_at': stamp(request_started_at)[0], 'observed_at': stamp(observed_at)[0],
              'identity': identity, 'sample': {'timestamp': timestamp, 'window_ns': str(int(nanos)),
              'cpu_nanocores': quantity(usage.get('cpu'), 'cpu'),
              'memory_working_set_bytes': quantity(usage.get('memory'), 'memory')}, 'get_attempts': 2}
    return validate(result)


def collect(pod_list, *, limit, fetch, now):
    """No application retry; at most two fixed kubectl acquisition intents.

    request_started_at precedes the first metrics operation. observed_at follows
    the second Pod operation for observed samples, or the last failed operation
    for unknown samples. The outer Pod-list observation keeps its original time.
    """
    identity = None
    attempts = 0
    started = observed = None
    try:
        require(isinstance(pod_list, dict) and isinstance(pod_list.get('items'), list), 'POD_LIST_INCOMPLETE')
        items = pod_list['items']
        require(type(limit) is int and 1 <= limit <= 50 and len(items) <= limit and
                not pod_list.get('metadata', {}).get('continue'), 'POD_LIST_INCOMPLETE')
        matches = [item for item in items if isinstance(item, dict) and
                   isinstance(item.get('metadata'), dict) and
                   isinstance(item['metadata'].get('labels'), dict) and
                   item['metadata']['labels'].get(LABEL) == TARGET]
        require(bool(matches), 'TARGET_NOT_FOUND')
        require(len(matches) == 1, 'TARGET_AMBIGUOUS')
        before = matches[0]
        identity, _ = pod_identity(before)
        started = stamp(now())[0]
        attempts = 1
        metrics = fetch('metrics', identity['pod_name'])
        attempts = 2
        after = fetch('pod', identity['pod_name'])
        observed = stamp(now())[0]
        return project(before, metrics, after, request_started_at=started, observed_at=observed)
    except PodUsageCleanupError:
        raise
    except Exception as error:
        reason = error.code if isinstance(error, PodUsageError) else 'READ_FAILED'
        if started is not None and observed is None:
            try:
                observed = stamp(now())[0]
            except Exception:
                started = None
        # A clock rollback is unknown, without publishing an invalid interval.
        if started is not None and observed is not None and stamp(started)[1] > stamp(observed)[1]:
            started = observed = None
            reason = 'TIME_INVALID'
        return validate(unknown(reason, identity=identity, attempts=attempts, started=started, observed=observed))


def _json(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'READ_FAILED')
        result[key] = value
    return result


def _command(arguments, *, deadline, environment, _raw=False, _max_bytes=MAX_BYTES):
    """Bound pipe reads while running; kill/reap only this newly owned group."""
    require(type(_max_bytes) is int and 0 < _max_bytes <= 2 * 1024 * 1024, 'READ_LIMIT')
    process = None
    selector = None
    original = None
    try:
        process = subprocess.Popen(['rtk', 'proxy', 'kubectl', '--context', CONTEXT, *arguments],
                                   cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        os.set_blocking(process.stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        chunks = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'READ_TIMEOUT')
            if not selector.select(remaining):
                raise PodUsageError('READ_TIMEOUT')
            chunk = os.read(process.stdout.fileno(), min(4096, _max_bytes + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            require(len(chunks) <= _max_bytes, 'READ_LIMIT')
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise PodUsageError('READ_TIMEOUT') from None
        require(code == 0, 'READ_FAILED')
        if _raw:
            return bytes(chunks)
        try:
            result = json.loads(chunks, object_pairs_hook=_json,
                                parse_constant=lambda _: (_ for _ in ()).throw(PodUsageError('READ_FAILED')))
        except (ValueError, UnicodeError, RecursionError):
            raise PodUsageError('READ_FAILED') from None
        require(isinstance(result, dict), 'READ_FAILED')
        return result
    except BaseException as error:
        original = error
        raise
    finally:
        cleanup_errors = []
        termination_errors = []
        try:
            if process is not None:
                try:
                    # poll() could reap an exited leader while its descendants
                    # still hold stdout open. Signal before reaping; returncode
                    # stays None until wait/poll releases this owned PID.
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass  # Exit raced with signal; wait still must confirm it.
                except BaseException as error:
                    termination_errors.append(error)
                try:
                    process.wait(timeout=CLEANUP_SECONDS)
                except BaseException as error:
                    termination_errors.append(error)
        except BaseException as error:
            termination_errors.append(error)
        finally:
            for item in (selector, process.stdout if process is not None else None):
                if item is not None:
                    try:
                        item.close()
                    except BaseException as error:
                        cleanup_errors.append(error)
        if original is None or isinstance(original, Exception):
            cancellation = next((error for error in termination_errors + cleanup_errors
                                 if not isinstance(error, Exception)), None)
            if cancellation is not None:
                raise cancellation
            if termination_errors:
                raise PodUsageCleanupError() from None
            if cleanup_errors and original is None:
                raise cleanup_errors[0]


def _command_bytes(arguments, *, deadline, environment, max_bytes):
    """Private bounded-byte transport; callers own their fixed path allowlist."""
    require(type(max_bytes) is int and 0 < max_bytes <= 2 * 1024 * 1024, 'READ_LIMIT')
    return _command(arguments, deadline=deadline, environment=environment,
                    _raw=True, _max_bytes=max_bytes)


def _local_server(*, deadline, environment):
    config = _command(['config', 'view', '--minify', '-o', 'json'], deadline=deadline, environment=environment)
    try:
        clusters = config['clusters']
        require(isinstance(clusters, list) and len(clusters) == 1, 'READ_FAILED')
        server = clusters[0]['cluster']['server']
        parsed = urlsplit(server)
        require(parsed.scheme == 'https' and parsed.hostname in ('127.0.0.1', 'localhost', '::1') and
                not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and
                parsed.path in ('', '/'), 'READ_FAILED')
    except (KeyError, TypeError, ValueError):
        raise PodUsageError('READ_FAILED') from None
    return server


def fixed_get(operation, pod_name):
    """Local config guard then one metrics/Pod acquisition; HTTP retry is internal."""
    require(operation in ('metrics', 'pod') and name(pod_name), 'READ_FAILED')
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL')
                   if key in os.environ}
    deadline = time.monotonic() + READ_SECONDS
    server = _local_server(deadline=deadline, environment=environment)
    prefix = '/apis/metrics.k8s.io/v1beta1' if operation == 'metrics' else '/api/v1'
    path = f'{prefix}/namespaces/{NAMESPACE}/pods/{pod_name}'
    return _command(['--server', server, '--request-timeout=6s', 'get', '--raw', path],
                    deadline=deadline, environment=environment)
