"""Deterministic read-only SRE observations, without model inference or remediation."""
from __future__ import annotations

import re
import copy
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.local_experiment.gateway.container_status import (
    ContainerObservationError, validate_container_observation,
)

CONTAINER_CRITICAL = frozenset({'CONTAINER_EXIT_NONZERO', 'CONTAINER_OOM_REPORTED',
                                'CONTAINER_IMAGE_PULL_WAIT', 'CONTAINER_BACKOFF_WAIT'})
CONTAINER_HISTORICAL = frozenset({'CONTAINER_PREVIOUS_EXIT_NONZERO', 'CONTAINER_PREVIOUS_OOM_REPORTED'})
CONTAINER_UNKNOWN = frozenset({'CONTAINER_OBSERVATION_UNKNOWN', 'CONTAINER_OBSERVATION_TRUNCATED'})


class DiagnosticError(ValueError):
    pass


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', value):
        raise DiagnosticError('invalid bounded diagnostic identity')
    return value


def count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise DiagnosticError('invalid diagnostic count')
    return value


def container_observation(pod: dict) -> dict | None:
    """A missing legacy field is not observed, never synthesized as complete."""
    if 'container_observation' not in pod:
        return None
    try:
        return copy.deepcopy(validate_container_observation(pod['container_observation']))
    except (ContainerObservationError, TypeError):
        raise DiagnosticError('invalid bounded container observation') from None


def resource_usage(report: dict) -> dict | None:
    """Legacy absence stays unobserved; reject raw or malformed usage fields."""
    if 'resource_usage' not in report:
        return None
    from tools.local_experiment.gateway.pod_usage import validate, timestamp_ns
    try:
        usage = validate(report['resource_usage'])
    except (ValueError, TypeError):
        raise DiagnosticError('invalid bounded resource usage') from None
    if usage['state'] == 'observed':
        try:
            ordered = (timestamp_ns(report.get('observed_at')) <= timestamp_ns(usage['request_started_at'])
                       <= timestamp_ns(usage['observed_at']))
        except (ValueError, TypeError):
            raise DiagnosticError('invalid resource observation time') from None
        if not ordered:
            raise DiagnosticError('resource request precedes its Pod observation')
    if usage['state'] == 'observed' and 'pods' in report:
        identity = usage['identity']
        matching = [pod for pod in report['pods'] if isinstance(pod, dict)
                    and pod.get('name') == identity['pod_name'] and pod.get('uid') == identity['pod_uid']]
        if (report.get('namespace') != identity['namespace'] or report.get('truncated') is not False
                or len(matching) != 1 or matching[0].get('phase') != 'Running'):
            raise DiagnosticError('resource usage does not match the observed Pod')
        details = container_observation(matching[0])
        rows = details['containers'] if details is not None else []
        if (len(rows) != 1 or rows[0]['name'] != identity['container']
                or rows[0]['state']['kind'] != 'running' or rows[0]['restarts'] != identity['restart_count']
                or rows[0]['state']['started_at'] != identity['started_at']):
            raise DiagnosticError('resource usage does not match the observed container')
    return usage


def container_code(state: dict, *, historical=False) -> str | None:
    if state['kind'] == 'terminated':
        if state['reason'] == 'OOMKilled':
            return 'CONTAINER_PREVIOUS_OOM_REPORTED' if historical else 'CONTAINER_OOM_REPORTED'
        if state['exit_code'] is not None and state['exit_code'] != 0:
            return 'CONTAINER_PREVIOUS_EXIT_NONZERO' if historical else 'CONTAINER_EXIT_NONZERO'
    if not historical and state['kind'] == 'waiting':
        if state['reason'] in ('ErrImagePull', 'ImagePullBackOff'):
            return 'CONTAINER_IMAGE_PULL_WAIT'
        if state['reason'] == 'CrashLoopBackOff':
            return 'CONTAINER_BACKOFF_WAIT'
    return None


def container_findings(pod: dict) -> list[dict]:
    observed = container_observation(pod)
    if observed is None:
        return []
    result = []
    name = identifier(pod.get('name'))
    if observed['status'] != 'complete':
        result.append({'code': 'CONTAINER_OBSERVATION_' + observed['status'].upper(),
                       'severity': 'warning', 'source': 'pods', 'subject': name,
                       'detail': 'Application-container details are incomplete; omitted states remain unknown.'})
    for row in observed['containers']:
        for field, label in (('state', 'current'), ('last_termination', 'last-termination')):
            state = row[field]
            code = container_code(state, historical=field == 'last_termination') if state else None
            if code:
                result.append({'code': code, 'severity': 'critical' if code in CONTAINER_CRITICAL else 'warning',
                               'source': 'pods', 'subject': name,
                               'detail': 'Reported container state only; root cause remains undetermined.',
                               'container': {'name': row['name'], 'observation': label,
                                             'status': observed['status'], 'ready': row['ready'],
                                             'restarts': row['restarts'], 'state': copy.deepcopy(state)}})
    return result


def verify_correlation(rows: list, logs: list, calls: list, *, event_id: str,
                       task_id: str, trace_id: str, span_id: str) -> bool:
    """A diagnostic succeeds only with all four retained, matching audit records."""
    expected_ids = {call['event_id'] for call in calls} | {event_id}
    expected_spans = {call['span_id']: 'mcp.' + call['tool'] for call in calls}
    expected_spans[span_id] = 'bear.sre.diagnose'
    if len(expected_ids) != 4 or len(expected_spans) != 4 or len(rows) != 4:
        return False
    if {row.get('event_id') for row in rows} != expected_ids:
        return False
    for row in rows:
        if (row.get('trace_id') != trace_id or row.get('task_id') != task_id
                or row.get('status') != 'success'
                or any(row.get(key, 'missing') is not None for key in
                       ('prompt_tokens', 'completion_tokens', 'inference_cost_estimate'))):
            return False
    matched_spans = {row.get('span_id') for row in logs
                     if row.get('task_id') == task_id and row.get('trace_id') == trace_id
                     and row.get('status') == 'success'
                     and row.get('action') == expected_spans.get(row.get('span_id'))}
    return set(expected_spans) <= matched_spans


def diagnose(doctor: dict, pods: dict, events: dict) -> dict:
    if doctor.get('profile') != 'local-experiment' or doctor.get('context') != 'colima-mac-studio-solo':
        raise DiagnosticError('doctor report is outside the local experiment scope')
    if pods.get('namespace') != 'shirokuma-dev' or events.get('namespace') != 'shirokuma-dev':
        raise DiagnosticError('Kubernetes observations are outside the allowed namespace')
    if not isinstance(doctor.get('checks'), list) or not doctor['checks']:
        raise DiagnosticError('doctor report has no checks')
    if not isinstance(pods.get('pods'), list) or not isinstance(events.get('events'), list):
        raise DiagnosticError('missing Kubernetes observation lists')
    findings = []
    def add(code, severity, source, subject, detail):
        findings.append({'code': code, 'severity': severity, 'source': source,
                         'subject': subject, 'detail': detail})

    intentional_flux = False
    for check in doctor['checks']:
        name = identifier(check.get('name'))
        status = check.get('status')
        if status not in {'healthy', 'informational', 'degraded'}:
            raise DiagnosticError('unknown doctor check status')
        if (name == 'flux' and status == 'informational'
                and 'root Kustomization/flux-system/flux-system is intentionally suspended for local-experiment'
                in check.get('summary', '')):
            intentional_flux = True
            add('INTENTIONAL_FLUX_SUSPENSION', 'informational', 'doctor', name,
                'The root reconciliation pause is part of the approved local development mode.')
        elif status == 'degraded':
            add('CHECK_DEGRADED', 'warning', 'doctor', name,
                'The bounded doctor check is degraded; this alone does not identify a root cause.')
        elif status == 'informational':
            add('INFORMATIONAL_CHECK', 'informational', 'doctor', name,
                'Doctor records an informational boundary separately from runtime health.')

    if not pods['pods']:
        add('POD_OBSERVATION_EMPTY', 'warning', 'pods', 'shirokuma-dev',
            'No pod observations were returned; component readiness cannot be confirmed.')
    for pod in pods['pods']:
        name, phase = identifier(pod.get('name')), identifier(pod.get('phase'))
        ready, total, restarts = (count(pod.get(key)) for key in ('ready_containers', 'total_containers', 'restarts'))
        if ready > total:
            raise DiagnosticError('ready container count exceeds total')
        if phase == 'Failed':
            add('WORKLOAD_FAILED', 'critical', 'pods', name, 'The observed pod phase is Failed.')
        elif phase not in {'Running', 'Succeeded'}:
            add('WORKLOAD_UNAVAILABLE', 'critical', 'pods', name,
                'The observed pod is not Running or a successfully completed job.')
        elif phase == 'Running' and (total == 0 or ready != total):
            add('WORKLOAD_NOT_READY', 'critical', 'pods', name,
                f'{ready} of {total} observed containers are ready.')
        if restarts:
            add('WORKLOAD_RESTARTED', 'warning', 'pods', name,
                f'{restarts} historical container restart(s); restart count alone does not prove a current outage.')
        findings.extend(container_findings(pod))
    if pods.get('truncated'):
        add('POD_OBSERVATION_TRUNCATED', 'warning', 'pods', 'shirokuma-dev',
            'The bounded pod list is incomplete; this report cannot assert namespace-wide readiness.')
    for event in events['events']:
        if event.get('type') == 'Warning':
            reason = identifier(event.get('reason'))
            kind = identifier(event.get('object_kind'))
            name = identifier(event.get('object_name'))
            add('WARNING_EVENT', 'warning', 'events', f'{kind}/{name}',
                f'Kubernetes reported {reason}; free-text messages are deliberately omitted.')
    findings.sort(key=lambda x: (x['severity'], x['code'], x['subject']))
    attention = any(x['severity'] in {'critical', 'warning'} and x['code'] not in CONTAINER_UNKNOWN for x in findings)
    usage = resource_usage(pods)
    return {'mode': 'deterministic-read-only', 'llm_inference': False,
            'health': 'attention-required' if attention else 'unknown' if any(
                x['code'] in CONTAINER_UNKNOWN for x in findings) or usage is not None and usage['state']=='unknown'
                else 'healthy-observed-scope',
            'intentional_flux_suspension': intentional_flux, 'findings': findings,
            'pod_count': len(pods['pods']), 'event_count': len(events['events']),
            'actions_executed': [],
            'limits': ['Observations are bounded snapshots, not a continuous monitor.',
                       'No root-cause inference from restart counts alone.',
                       'Legacy container details are not observed; init and ephemeral containers are excluded.',
                       'Reported OOM reason is not a root-cause diagnosis; exit 137 alone is not an OOM report.',
                       'No workload mutation, remediation, PR, or external publication.']}
