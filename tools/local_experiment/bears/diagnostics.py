"""Deterministic read-only SRE observations, without model inference or remediation."""
from __future__ import annotations

import re
from typing import Any


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
    attention = any(x['severity'] in {'critical', 'warning'} for x in findings)
    return {'mode': 'deterministic-read-only', 'llm_inference': False,
            'health': 'attention-required' if attention else 'healthy-observed-scope',
            'intentional_flux_suspension': intentional_flux, 'findings': findings,
            'pod_count': len(pods['pods']), 'event_count': len(events['events']),
            'actions_executed': [],
            'limits': ['Observations are bounded snapshots, not a continuous monitor.',
                       'No root-cause inference from restart counts alone.',
                       'No workload mutation, remediation, PR, or external publication.']}
