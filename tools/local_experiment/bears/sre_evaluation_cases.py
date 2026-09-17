"""Fixed synthetic SRE telemetry contrasts; no reads, model calls or raw logs.

The registered dataset identifies synthetic inputs and literal scoring oracles,
not the actual HTTP prompt or general diagnostic quality. Checks have no order.
"""
from __future__ import annotations

import copy
import hashlib
import json

import sre_contract as C
from tools.local_experiment.gateway import pod_logs as L, pod_usage as U

SIGNAL_SUITE = 'observation-signals-v1'
SIGNAL_CASE_IDS = (
    'signals_known_zero', 'signals_usage_unknown', 'signals_logs_unknown',
    'signals_noisy_headers', 'signals_runtime_critical',
    'signals_current_container_failure', 'signals_previous_oom',
    'signals_dual_critical',
)
SIGNAL_DATASET_SHA256 = 'b2757e1e198c1212c0e21459a6f882967168db34e6371a3c647337922175c732'
_POD = 'k8s_read_pods'
_RUNTIME = 'local_platform_status'


def _expected(assessment, findings=(), required=(), allowed=()):
    return {'assessment': assessment, 'root_cause': 'undetermined',
            'findings': [{'code': code, 'severity': severity, 'observation': observation}
                         for code, severity, observation in findings],
            'required_tools': list(required), 'allowed_tools': list(allowed)}


# Literal truth is independent of build_evidence/validate_triage. The first four
# deliberately do not create a finding from metrics or console text counts.
EXPECTED = {
    'signals_known_zero': _expected('healthy-observed-scope'),
    'signals_usage_unknown': _expected('unknown'),
    'signals_logs_unknown': _expected('healthy-observed-scope'),
    'signals_noisy_headers': _expected('healthy-observed-scope'),
    'signals_runtime_critical': _expected('attention-required',
        (('RUNTIME_NOT_READY', 'critical', None),), (_RUNTIME,), (_RUNTIME,)),
    'signals_current_container_failure': _expected('attention-required',
        (('CONTAINER_EXIT_NONZERO', 'critical', 'current'),
         ('WORKLOAD_FAILED', 'critical', None)), (_POD,), (_POD, 'k8s_read_events')),
    'signals_previous_oom': _expected('attention-required',
        (('CONTAINER_PREVIOUS_OOM_REPORTED', 'warning', 'last-termination'),
         ('WORKLOAD_RESTARTED', 'warning', None)), (), (_POD, 'k8s_read_events')),
    'signals_dual_critical': _expected('attention-required',
        (('CONTAINER_EXIT_NONZERO', 'critical', 'current'),
         ('RUNTIME_NOT_READY', 'critical', None),
         ('WORKLOAD_FAILED', 'critical', None)), (_POD, _RUNTIME),
        (_POD, 'k8s_read_events', _RUNTIME)),
}


def _snapshots():
    start = '2026-09-17T01:00:00.000000Z'
    stamp = '2026-09-17T01:59:59.000000Z'
    identity = {'namespace': 'shirokuma-dev', 'pod_name': 'trino-local-synthetic',
                'pod_uid': '11111111-1111-4111-8111-111111111111', 'container': 'trino',
                'container_id_sha256': 'a' * 64, 'restart_count': 0, 'started_at': start}
    usage = {'schema_version': 1, 'scope': 'fixed-trino-container', 'state': 'observed',
             'reason': None, 'binding': 'name-time-correlated', 'identity': identity,
             'request_started_at': '2026-09-17T01:59:59.000000000Z',
             'observed_at': '2026-09-17T02:00:00.000000000Z', 'get_attempts': 2,
             'sample': {'timestamp': '2026-09-17T01:59:58.000000000Z',
                        'window_ns': '30000000000', 'cpu_nanocores': '0',
                        'memory_working_set_bytes': '0'}}
    logs = {'schema_version': 1, 'scope': L.SCOPE, 'state': 'observed', 'reason': None,
            'binding': 'name-time-correlated', 'identity': copy.deepcopy(identity),
            'request_started_at': '2026-09-17T02:00:01.000000000Z',
            'observed_at': '2026-09-17T02:00:02.000000000Z',
            'since_time': '2026-09-17T01:55:01Z', 'get_attempts': 2,
            'fragment': {'returned_bytes': 0, 'complete_lines': 0, 'header_lines': 0,
                         'warn_header_lines': 0, 'error_header_lines': 0, 'unparsed_lines': 0,
                         'first_prefix_at': None, 'last_prefix_at': None},
            'limitations': list(L.LIMITATIONS)}
    container = {'name': 'trino', 'ready': True, 'restarts': 0,
                 'state': {'kind': 'running', 'reason': None, 'exit_code': None,
                           'started_at': start, 'finished_at': None}, 'last_termination': None}
    doctor = {'profile': 'local-experiment', 'context': 'colima-mac-studio-solo',
              'observed_at': stamp, 'checks': [
                  {'name': 'cluster', 'status': 'healthy', 'summary': 'synthetic fixture'},
                  {'name': 'flux', 'status': 'informational', 'summary': C.INTENTIONAL_FLUX_MARKER}]}
    pods = {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False,
            'pods': [{'name': identity['pod_name'], 'uid': identity['pod_uid'],
                      'phase': 'Running', 'ready_containers': 1, 'total_containers': 1,
                      'restarts': 0, 'container_observation': {'status': 'complete',
                          'scope': 'application-containers', 'containers': [container]}}],
            'resource_usage': usage, 'log_signals': logs}
    events = {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False, 'events': []}
    platform = {'profile': 'local-experiment', 'observed_at': stamp, 'ready': True,
                'components': {name: {'ready': True, 'status': 'ready'} for name in C.COMPONENTS}}
    return doctor, pods, events, platform


def _noisy(pods):
    pods['resource_usage']['sample'].update(cpu_nanocores='4000000000',
                                           memory_working_set_bytes='8589934592')
    pods['log_signals']['fragment'].update(returned_bytes=32000, complete_lines=200,
        header_lines=100, warn_header_lines=50, error_header_lines=50, unparsed_lines=100,
        first_prefix_at='2026-09-17T02:00:00.000000000Z',
        last_prefix_at='2026-09-17T02:00:01.000000000Z')


def validate_case(case):
    """Reject fixture/oracle drift before inference; return an independent copy."""
    if type(case) is not dict or set(case) != {'case_id', 'evidence', 'expected'}:
        raise ValueError('invalid fixed SRE signal case')
    name = case['case_id']
    if type(name) is not str or name not in EXPECTED or case['expected'] != EXPECTED[name]:
        raise ValueError('invalid fixed SRE signal oracle')
    evidence = C.validate_evidence(case['evidence'])
    facts = [{'code': row['code'], 'severity': row['severity'],
              'observation': row.get('container', {}).get('observation')}
             for row in evidence['findings']]
    if evidence['health'] != EXPECTED[name]['assessment'] or facts != EXPECTED[name]['findings']:
        raise ValueError('fixed SRE signal evidence differs from oracle')
    return copy.deepcopy(case)


def signal_cases():
    """Return the eight fixed cases in registered order, with literal oracles."""
    result = []
    for name in SIGNAL_CASE_IDS:
        doctor, pods, events, platform = _snapshots()
        if name in {'signals_usage_unknown', 'signals_logs_unknown'}:
            pods['log_signals'] = L.unknown('READ_FAILED')
        if name == 'signals_usage_unknown':
            pods['resource_usage'] = U.unknown('READ_FAILED')
        if name in {'signals_noisy_headers', 'signals_runtime_critical', 'signals_previous_oom'}:
            _noisy(pods)
        if name in {'signals_runtime_critical', 'signals_dual_critical'}:
            platform['ready'] = False
            platform['components']['superset'] = {'ready': False, 'status': 'not-ready'}
        row = pods['pods'][0]
        container = row['container_observation']['containers'][0]
        if name in {'signals_current_container_failure', 'signals_dual_critical'}:
            row.update(phase='Failed', ready_containers=0)
            container['ready'] = False
            container['state'].update(kind='terminated', reason='Error', exit_code=137,
                                       finished_at='2026-09-17T01:59:00.000000Z')
            pods['resource_usage'] = U.unknown('POD_NOT_RUNNING')
            pods['log_signals'] = L.unknown('POD_NOT_RUNNING')
        if name == 'signals_previous_oom':
            row['restarts'] = container['restarts'] = 1
            container['last_termination'] = {'kind': 'terminated', 'reason': 'OOMKilled',
                'exit_code': 137, 'started_at': '2026-09-17T00:00:00.000000Z',
                'finished_at': '2026-09-17T00:59:00.000000Z'}
            for key in ('resource_usage', 'log_signals'):
                pods[key]['identity']['restart_count'] = 1
        case = {'case_id': name, 'evidence': C.build_evidence(doctor, pods, events, platform),
                'expected': copy.deepcopy(EXPECTED[name])}
        result.append(validate_case(case))
    return result


def dataset_sha256(cases=None):
    """Canonical synthetic input/oracle identity; does not hash an HTTP prompt."""
    value = signal_cases() if cases is None else cases
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def validate_signal_triage(case, response):
    """Use unchanged triage rules plus literal assessment/tool oracles; no ordering."""
    case = validate_case(case)
    proposal = C.validate_triage(response, case['evidence'])
    expected = case['expected']
    tools = {item['tool'] for item in proposal['checks']}
    if (proposal['assessment'] != expected['assessment']
            or proposal['root_cause'] != expected['root_cause']
            or not set(expected['required_tools']) <= tools
            or not tools <= set(expected['allowed_tools'])):
        raise ValueError('SRE signal triage differs from fixed oracle')
    return copy.deepcopy(proposal)
