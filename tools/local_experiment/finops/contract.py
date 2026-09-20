"""Local resource observations and recorded inference estimates; no budget actions."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import json
import math
import re
import uuid

from tools.local_experiment.gateway import pod_usage as U

COMPONENTS = {'trino': ('shirokuma-dev', 'trino-local', 'trino'),
              'polaris': ('shirokuma-dev', 'polaris', 'polaris'),
              'seaweedfs': ('shirokuma-storage', 'seaweedfs', 'seaweedfs')}
DISKS = ('host', 'colima')
METRICS = ('prompt_tokens', 'completion_tokens', 'duration_ms', 'inference_cost_estimate',
           'cpu_seconds', 'memory_mb_seconds')
INTEGER_METRICS = frozenset(METRICS[:3])
PUBLIC_METRICS = {key: 'event_duration_ms' if key == 'duration_ms' else key for key in METRICS}
REASONS = frozenset(('READ_FAILED', 'READ_LIMIT', 'READ_TIMEOUT', 'CLEANUP_UNVERIFIED',
                    'SCOPE_INVALID', 'TARGET_MISSING', 'TARGET_AMBIGUOUS', 'IDENTITY_CHANGED',
                    'SAMPLE_INVALID', 'SAMPLE_STALE', 'DISK_INVALID', 'INFERENCE_INVALID'))
LIMITS = [
    'LOCAL_EXPERIMENT_READ_ONLY', 'THREE_FIXED_COMPONENTS_NOT_FULL_PLATFORM_COVERAGE',
    'PRIMARY_CONTAINER_ONLY_INIT_AND_SIDECAR_USAGE_UNSUPPORTED',
    'NAME_TIME_CORRELATION_NOT_CRYPTOGRAPHIC_METRICS_IDENTITY',
    'POINT_SAMPLES_NOT_CPU_TIME_OR_TRENDS', 'PVC_AND_PER_COMPONENT_STORAGE_ATTRIBUTION_UNSUPPORTED',
    'HOST_AND_VM_SHARE_PHYSICAL_STORAGE_DO_NOT_SUM', 'FREE_SPACE_EXCLUDES_UNMEASURED_RECLAIMABLE_SPACE',
    'DECLARED_EVENTS_INCLUDE_SYNTHETIC_RECORDS_NOT_PROOF_OF_ACTUAL_MODEL_CALLS',
    'EVENT_DURATION_NOT_NECESSARILY_PROVIDER_INFERENCE_LATENCY', 'COST_CURRENCY_NOT_RECORDED',
    'LOCAL_INFERENCE_PROXIES_ARE_OPTIONAL_PRODUCER_RECORDS_NOT_ENERGY_OR_MONEY',
    'NO_AUTOMATIC_STOP_DELETE_BUDGET_OVERRIDE_OR_PR_GATE']
UNITS = {'prompt_tokens': 'tokens', 'completion_tokens': 'tokens', 'event_duration_ms': 'milliseconds',
         'inference_cost_estimate': 'producer-defined-estimate', 'cpu_seconds': 'seconds',
         'memory_mb_seconds': 'producer-recorded-MB-seconds; MB-definition-not-recorded'}
MAX_PUBLIC_BYTES = 256 * 1024


class FinOpsError(ValueError):
    pass


def require(condition, code='INFERENCE_INVALID'):
    if not condition: raise FinOpsError(code)


def stamp(value):
    try: return U.stamp(value)
    except (ValueError, TypeError): raise FinOpsError('SAMPLE_INVALID') from None


def integer(value, maximum=2**63-1):
    require(type(value) is str and re.fullmatch(r'0|[1-9][0-9]*', value) is not None and
            len(value) <= 40 and int(value) <= maximum)
    return int(value)


def unknown_component(component, reason):
    require(component in COMPONENTS and reason in REASONS)
    return {'component': component, 'state': 'unknown', 'reason': reason, 'request_started_at': None,
            'observed_at': None, 'identity': None, 'sample': None, 'binding': None}


def _identity(component, pod):
    namespace, label, container = COMPONENTS[component]
    require(type(pod) is dict and type(pod.get('metadata')) is dict and type(pod.get('status')) is dict,
            'SCOPE_INVALID')
    meta, status = pod['metadata'], pod['status']
    require(meta.get('namespace') == namespace and U.name(meta.get('name')) and
            meta.get('labels', {}).get('app.kubernetes.io/name') == label and
            meta.get('deletionTimestamp') is None and status.get('phase') == 'Running', 'SCOPE_INVALID')
    if component == 'polaris':
        require(meta['labels'].get('app.kubernetes.io/component') == 'catalog-server', 'SCOPE_INVALID')
    try: require(str(uuid.UUID(meta.get('uid'))) == meta['uid'], 'SCOPE_INVALID')
    except (ValueError, TypeError, AttributeError): raise FinOpsError('SCOPE_INVALID') from None
    spec = pod.get('spec', {})
    containers, states = spec.get('containers'), status.get('containerStatuses')
    require(type(containers) is list and len(containers) == 1 and containers[0].get('name') == container and
            type(states) is list and len(states) == 1 and states[0].get('name') == container and
            not spec.get('ephemeralContainers'), 'SCOPE_INVALID')
    state = states[0]
    require(type(state.get('restartCount')) is int and 0 <= state['restartCount'] <= 2**31-1 and
            type(state.get('state')) is dict and set(state['state']) == {'running'}, 'SCOPE_INVALID')
    started, started_ns = stamp(state['state']['running'].get('startedAt'))
    created = stamp(meta.get('creationTimestamp'))[1]
    require(created <= started_ns and type(state.get('containerID')) is str and
            re.fullmatch(r'(?:docker|containerd)://[0-9a-f]{64}', state['containerID']), 'SCOPE_INVALID')
    return {'namespace': namespace, 'pod_name': meta['name'], 'pod_uid': meta['uid'], 'container': container,
            'container_id_sha256': hashlib.sha256(state['containerID'].encode()).hexdigest(),
            'restart_count': state['restartCount'], 'started_at': started}, created


def project_component(component, before, metrics, after, *, request_started_at, observed_at):
    require(component in COMPONENTS, 'SCOPE_INVALID')
    identity, created = _identity(component, before)
    require((identity, created) == _identity(component, after), 'IDENTITY_CHANGED')
    require(type(metrics) is dict and metrics.get('kind') == 'PodMetrics' and
            metrics.get('apiVersion') == 'metrics.k8s.io/v1beta1', 'SAMPLE_INVALID')
    meta, containers = metrics.get('metadata', {}), metrics.get('containers')
    require(meta.get('namespace') == identity['namespace'] and meta.get('name') == identity['pod_name'] and
            meta.get('uid', identity['pod_uid']) == identity['pod_uid'] and type(containers) is list and
            len(containers) == 1 and containers[0].get('name') == identity['container'], 'SAMPLE_INVALID')
    window = metrics.get('window')
    require(type(window) is str and len(window) <= 32 and
            re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?s', window), 'SAMPLE_INVALID')
    ns = Fraction(window[:-1]) * 10**9
    require(ns.denominator == 1 and 0 < ns <= 120 * 10**9, 'SAMPLE_INVALID')
    usage = containers[0].get('usage', {})
    try:
        sample = {'timestamp': stamp(metrics.get('timestamp'))[0], 'window_ns': str(int(ns)),
                  'cpu_nanocores': U.quantity(usage.get('cpu'), 'cpu'),
                  'memory_working_set_bytes': U.quantity(usage.get('memory'), 'memory')}
    except ValueError: raise FinOpsError('SAMPLE_INVALID') from None
    result = {'component': component, 'state': 'observed', 'reason': None,
              'request_started_at': stamp(request_started_at)[0], 'observed_at': stamp(observed_at)[0],
              'identity': identity, 'sample': sample, 'binding': 'name-time-correlated'}
    return validate_component(result)


def validate_component(value):
    require(type(value) is dict and set(value) == set(unknown_component('trino', 'READ_FAILED')) and
            value['component'] in COMPONENTS, 'SAMPLE_INVALID')
    if value['state'] == 'unknown':
        require(value == unknown_component(value['component'], value['reason']), 'SAMPLE_INVALID')
        return deepcopy(value)
    require(value['state'] == 'observed' and value['reason'] is None and
            value['binding'] == 'name-time-correlated', 'SAMPLE_INVALID')
    identity, sample = value['identity'], value['sample']
    namespace, _, container = COMPONENTS[value['component']]
    require(type(identity) is dict and set(identity) == {'namespace', 'pod_name', 'pod_uid', 'container',
            'container_id_sha256', 'restart_count', 'started_at'} and identity['namespace'] == namespace and
            identity['container'] == container and U.name(identity['pod_name']) and
            type(identity['container_id_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', identity['container_id_sha256']) and
            type(identity['restart_count']) is int and 0 <= identity['restart_count'] <= 2**31-1, 'SAMPLE_INVALID')
    try: require(str(uuid.UUID(identity['pod_uid'])) == identity['pod_uid'], 'SAMPLE_INVALID')
    except (ValueError, TypeError, AttributeError): raise FinOpsError('SAMPLE_INVALID') from None
    require(type(sample) is dict and set(sample) == {'timestamp', 'window_ns', 'cpu_nanocores',
            'memory_working_set_bytes'}, 'SAMPLE_INVALID')
    window = integer(sample['window_ns']); integer(sample['cpu_nanocores']); integer(sample['memory_working_set_bytes'])
    start, end, sample_at, began = [stamp(v)[1] for v in
        (value['request_started_at'], value['observed_at'], sample['timestamp'], identity['started_at'])]
    require(0 <= end-start <= 20*10**9 and 0 < window <= 120*10**9 and
            sample_at-window > began, 'SAMPLE_INVALID')
    require(-5*10**9 <= end-sample_at <= 120*10**9, 'SAMPLE_STALE')
    return deepcopy(value)


def disk(scope, total=None, free=None, *, observed_at, reason=None):
    require(scope in DISKS and (reason is None or reason in REASONS), 'DISK_INVALID')
    value = {'scope': scope, 'state': 'unknown' if reason else 'observed', 'reason': reason,
             'observed_at': stamp(observed_at)[0], 'total_bytes': None, 'free_bytes': None,
             'free_percent': None, 'severity': 'unknown'}
    if reason: return value
    require(type(total) is int and type(free) is int and 0 < total <= 2**63-1 and 0 <= free <= total, 'DISK_INVALID')
    severity = 'critical' if free*100 <= total*15 else 'low' if free*100 <= total*20 else \
               'warning' if free*100 <= total*25 else 'normal'
    value.update(total_bytes=str(total), free_bytes=str(free), free_percent=round(free*100/total, 6), severity=severity)
    return value


def window(end):
    # Report windows use host UTC milliseconds, fixed once before the query.
    # The recorded query window is millisecond precision; Python 3.9 accepts
    # microseconds, whereas the shared timestamp parser canonicalizes to ns.
    parsed = datetime.fromisoformat(stamp(end)[0][:26] + '+00:00')
    def fmt(value): return value.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return {'hours': 24, 'start': fmt(parsed-timedelta(hours=24)), 'end': fmt(parsed)}


def inference(rows, *, end, reason=None):
    value = {'state': 'unknown' if reason else 'observed', 'reason': reason, 'source': 'agenthouse-declared-model-events-including-synthetic',
             'window': window(end), 'currency': None, 'units': deepcopy(UNITS), 'groups': [],
             'recorded_model_events': None if reason else 0}
    require(reason is None or reason in REASONS)
    if reason: return value
    require(type(rows) is list and len(rows) <= 50, 'READ_LIMIT')
    seen = set()
    expected = {'model_provider', 'model_name', 'events'} | {m+s for m in METRICS for s in ('_known', '_sum')}
    for row in rows:
        require(type(row) is dict and set(row) == expected)
        identity = tuple(row[k] for k in ('model_provider', 'model_name'))
        require(all(type(v) is str and 0 < len(v.encode()) <= 256 and
                    all(ord(c) >= 32 and ord(c) != 127 for c in v) for v in identity) and
                identity[0] != 'none' and identity not in seen)
        seen.add(identity)
        events = integer(row['events'], 10**9); require(events > 0)
        projected = {'model_provider': identity[0], 'model_name': identity[1], 'recorded_model_events': events}
        for metric in METRICS:
            known = integer(row[metric+'_known'], events)
            total = row[metric+'_sum']
            if known == 0: require(total is None)
            elif metric in INTEGER_METRICS: integer(total, 2**128-1)
            else: require(type(total) in (int, float) and math.isfinite(total) and total >= 0)
            require(metric != 'duration_ms' or known == events)
            projected[PUBLIC_METRICS[metric]] = {'known_events': known, 'unknown_events': events-known,
                'known_sum': total, 'total': total if known == events else None,
                'reason': None if known == events else 'NOT_RECORDED_FOR_ALL_EVENTS'}
        value['groups'].append(projected); value['recorded_model_events'] += events
    value['groups'].sort(key=lambda row: (row['model_provider'], row['model_name']))
    return value


def build_report(components, disks, recorded, *, observed_at):
    components = [validate_component(row) for row in components]
    require([row['component'] for row in components] == list(COMPONENTS), 'SCOPE_INVALID')
    require([row['scope'] for row in disks] == list(DISKS), 'SCOPE_INVALID')
    recommendations = [{'scope': row['scope'], 'code': 'DEFER_NEW_LARGE_EXPERIMENTS',
                        'message': '空き容量を確認し、新しい大容量の取得・実験を控えてください。'}
                       for row in disks if row['severity'] in ('warning', 'low', 'critical')]
    value = {'schema_version': 1, 'command': 'finops report',
             'result': 'complete' if all(row['state'] == 'observed' for row in components+disks+[recorded]) else 'partial',
             'observed_at': stamp(observed_at)[0], 'components': components, 'disks': deepcopy(disks),
             'inference': deepcopy(recorded), 'recommendations': recommendations, 'limits': deepcopy(LIMITS)}
    require(len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) <= MAX_PUBLIC_BYTES, 'READ_LIMIT')
    return value


def validate_public(value):
    require(type(value) is dict and set(value) == {'schema_version', 'command', 'result', 'observed_at',
            'components', 'disks', 'inference', 'recommendations', 'limits'})
    disks = [disk(row['scope'], None if row['total_bytes'] is None else integer(row['total_bytes']),
                  None if row['free_bytes'] is None else integer(row['free_bytes']),
                  observed_at=row['observed_at'], reason=row['reason']) for row in value['disks']]
    recorded = value['inference']; raw = []
    for row in recorded['groups']:
        raw.append({'model_provider': row['model_provider'], 'model_name': row['model_name'],
                    'events': str(row['recorded_model_events']),
                    **{m+'_known': str(row[PUBLIC_METRICS[m]]['known_events']) for m in METRICS},
                    **{m+'_sum': row[PUBLIC_METRICS[m]]['known_sum'] for m in METRICS}})
    expected = build_report(value['components'], disks,
        inference(raw, end=recorded['window']['end'], reason=recorded['reason']), observed_at=value['observed_at'])
    require(json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False))
    return deepcopy(value)
