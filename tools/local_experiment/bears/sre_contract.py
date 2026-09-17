"""Pure contracts for bounded evidence, read selection, and snapshot comparison."""
from __future__ import annotations

from datetime import datetime
import re

from diagnostics import (DiagnosticError, diagnose, identifier, count, container_observation,
                         container_code, resource_usage, CONTAINER_CRITICAL, CONTAINER_HISTORICAL, CONTAINER_UNKNOWN)

TOOLS = ('shirokuma_doctor', 'k8s_read_pods', 'k8s_read_events', 'local_platform_status')
SOURCES = dict(zip(TOOLS, ('doctor', 'pods', 'events', 'platform')))
COMPONENTS = ('lakehouse', 'superset', 'metadata', 'agenthouse', 'loki')
MAX_FINDINGS = 24
INTENTIONAL_FLUX_MARKER = 'root Kustomization/flux-system/flux-system is intentionally suspended for local-experiment'
POD_CRITICAL = {'WORKLOAD_FAILED', 'WORKLOAD_UNAVAILABLE', 'WORKLOAD_NOT_READY'} | CONTAINER_CRITICAL
RUNTIME_CRITICAL = {'RUNTIME_NOT_READY'}
UNKNOWN_FINDINGS = {'EVENT_OBSERVATION_TRUNCATED', 'OBSERVATION_UNKNOWN', 'RUNTIME_STATUS_UNKNOWN'} | CONTAINER_UNKNOWN
ALLOWED = {
    'CHECK_DEGRADED': (TOOLS[0],),
    'WORKLOAD_FAILED': (TOOLS[1], TOOLS[2]),
    'WORKLOAD_UNAVAILABLE': (TOOLS[1], TOOLS[2]),
    'WORKLOAD_NOT_READY': (TOOLS[1], TOOLS[2]),
    'WORKLOAD_RESTARTED': (TOOLS[1], TOOLS[2]),
    'POD_OBSERVATION_EMPTY': (TOOLS[1],),
    'POD_OBSERVATION_TRUNCATED': (TOOLS[1],),
    'WARNING_EVENT': (TOOLS[2], TOOLS[1]),
    'EVENT_OBSERVATION_TRUNCATED': (TOOLS[2],),
    'RUNTIME_NOT_READY': (TOOLS[3],),
    'RUNTIME_STATUS_UNKNOWN': (TOOLS[3],),
    'OBSERVATION_UNKNOWN': TOOLS,
}
ALLOWED.update({code: (TOOLS[1],) for code in CONTAINER_CRITICAL | CONTAINER_HISTORICAL | CONTAINER_UNKNOWN})


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DiagnosticError('invalid bounded SRE contract keys')


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise DiagnosticError('invalid observation timestamp')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise DiagnosticError('invalid observation timestamp') from None


def _uid(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise DiagnosticError('invalid observation identity')
    return value


def _platform(report):
    """Validate the fixed five-component observation without retaining raw reasons."""
    if not isinstance(report, dict) or report.get('profile') != 'local-experiment':
        raise DiagnosticError('platform is outside the local experiment scope')
    _timestamp(report.get('observed_at'))
    _keys(report.get('components'), COMPONENTS)
    components = {}
    for name in COMPONENTS:
        row = report['components'][name]
        _keys(row, ('ready', 'status'))
        if (type(row['ready']) is not bool or not isinstance(row['status'], str)
                or row['status'] not in {'ready', 'not-ready', 'unknown'}
                or row['ready'] != (row['status'] == 'ready')):
            raise DiagnosticError('invalid runtime component status')
        components[name] = {'ready': row['ready'], 'status': row['status']}
    ready = report.get('ready')
    if type(ready) is not bool or ready != all(row['ready'] for row in components.values()):
        raise DiagnosticError('runtime aggregate readiness disagrees with components')
    return {'profile': 'local-experiment', 'observed_at': report.get('observed_at'),
            'ready': ready, 'components': components}


def _observation(source, report):
    if source == 'platform':
        platform = _platform(report)
        unknown = (platform['observed_at'] is None
                   or any(row['status'] == 'unknown' for row in platform['components'].values()))
        return {'observed_at': platform['observed_at'], 'completeness': 'unknown' if unknown else 'complete',
                'count': len(COMPONENTS)}
    if not isinstance(report, dict):
        raise DiagnosticError('missing bounded observation')
    values = report.get('checks' if source == 'doctor' else source)
    if not isinstance(values, list):
        raise DiagnosticError('missing bounded observation list')
    if source != 'doctor' and report.get('namespace') != 'shirokuma-dev':
        raise DiagnosticError('observation is outside the local namespace')
    if source == 'doctor' and (report.get('profile') != 'local-experiment'
                              or report.get('context') != 'colima-mac-studio-solo'):
        raise DiagnosticError('doctor is outside the local experiment scope')
    stamp = report.get('observed_at')
    _timestamp(stamp)
    truncated = report.get('truncated') if source != 'doctor' else False
    if truncated is not None and type(truncated) is not bool:
        raise DiagnosticError('invalid observation truncation flag')
    completeness = ('unknown' if stamp is None or truncated is None else
                    'truncated' if truncated else
                    'empty' if not values and source != 'events' else 'complete')
    result = {'observed_at': stamp, 'completeness': completeness, 'count': len(values)}
    if source == 'pods':
        details = dict(scope='application-containers', complete=0, truncated=0, unknown=0, not_observed=0)
        for pod in values:
            if not isinstance(pod, dict):
                raise DiagnosticError('invalid Pod observation')
            observed = container_observation(pod)
            details[observed['status'] if observed is not None else 'not_observed'] += 1
        result['container_details'] = details
        usage = resource_usage(report)
        if usage is not None:
            result['resource_usage'] = usage
    return result


def _allowed(code, subject):
    if code == 'OBSERVATION_UNKNOWN':
        if subject not in SOURCES.values():
            raise DiagnosticError('invalid missing observation source')
        return [next(tool for tool, source in SOURCES.items() if source == subject)]
    if code == 'WARNING_EVENT' and not subject.startswith('Pod/'):
        return [TOOLS[2]]
    if code in RUNTIME_CRITICAL | {'RUNTIME_STATUS_UNKNOWN'} and subject not in COMPONENTS:
        raise DiagnosticError('invalid runtime component subject')
    return list(ALLOWED[code])


def _unknown_container_coverage(observations):
    details = observations['pods'].get('container_details', {})
    return bool(details.get('unknown') or details.get('truncated'))


def _unknown_usage_coverage(observations):
    usage = observations['pods'].get('resource_usage')
    return usage is not None and usage['state'] == 'unknown'


def build_evidence(doctor: dict, pods: dict, events: dict, platform: dict | None = None) -> dict:
    """Project no raw summaries, messages, logs, commands, credentials or causes."""
    reports = dict(zip(('doctor', 'pods', 'events'), (doctor, pods, events)))
    observations = {source: _observation(source, report) for source, report in reports.items()}
    observations['platform'] = (_observation('platform', platform) if platform is not None else
                                {'observed_at': None, 'completeness': 'unknown', 'count': 0})
    baseline = diagnose(doctor, pods, events)
    findings = [{key: item[key] for key in ('code', 'severity', 'subject', 'container') if key in item}
                for item in baseline['findings'] if item['severity'] in {'warning', 'critical'}]
    if events.get('truncated'):
        findings.append({'code': 'EVENT_OBSERVATION_TRUNCATED', 'severity': 'warning', 'subject': 'events'})
    if platform is not None:
        for name, row in _platform(platform)['components'].items():
            if row['status'] != 'ready':
                findings.append({'code': 'RUNTIME_NOT_READY' if row['status'] == 'not-ready' else 'RUNTIME_STATUS_UNKNOWN',
                                 'severity': 'critical' if row['status'] == 'not-ready' else 'warning', 'subject': name})
    for source, item in observations.items():
        if item['completeness'] == 'unknown' and (source != 'platform' or item['observed_at'] is None):
            findings.append({'code': 'OBSERVATION_UNKNOWN', 'severity': 'warning', 'subject': source})
    findings.sort(key=lambda item: (item['severity'] != 'critical', item['code'], item['subject'],
                                    item.get('container', {}).get('name', '')))
    if sum(item['severity'] == 'critical' for item in findings) > MAX_FINDINGS:
        raise DiagnosticError('critical findings exceed bounded triage capacity')
    result = {
        'schema_version': 1, 'health': baseline['health'],
        'intentional_flux_suspension': baseline['intentional_flux_suspension'],
        'observations': observations,
        'findings': [dict(item, id=f'f{index:02d}', allowed_tools=_allowed(item['code'], item['subject']))
                     for index, item in enumerate(findings[:MAX_FINDINGS], 1)],
        'omitted_findings': max(0, len(findings) - MAX_FINDINGS),
    }
    result['health'] = ('attention-required' if any(x['code'] not in UNKNOWN_FINDINGS for x in findings)
                        else 'unknown' if findings or _unknown_container_coverage(observations) or _unknown_usage_coverage(observations)
                        or any(item['completeness'] != 'complete' for item in observations.values())
                        else 'healthy-observed-scope')
    validate_evidence(result)
    return result


def validate_evidence(evidence: dict) -> dict:
    """Revalidate the exact model-facing projection at the provider boundary."""
    _keys(evidence, ('schema_version', 'health', 'intentional_flux_suspension',
                     'observations', 'findings', 'omitted_findings'))
    if type(evidence['schema_version']) is not int or evidence['schema_version'] != 1:
        raise DiagnosticError('unknown SRE evidence schema')
    if not isinstance(evidence['health'], str) or evidence['health'] not in {'attention-required', 'healthy-observed-scope', 'unknown'}:
        raise DiagnosticError('invalid observed health')
    if type(evidence['intentional_flux_suspension']) is not bool:
        raise DiagnosticError('invalid intentional suspension flag')
    count(evidence['omitted_findings'])
    _keys(evidence['observations'], SOURCES.values())
    for source, observation in evidence['observations'].items():
        detailed = source == 'pods' and isinstance(observation, dict) and 'container_details' in observation
        usage_present = source == 'pods' and isinstance(observation, dict) and 'resource_usage' in observation
        _keys(observation, ('observed_at', 'completeness', 'count') + (('container_details',) if detailed else ())
              + (('resource_usage',) if usage_present else ()))
        _timestamp(observation['observed_at'])
        count(observation['count'])
        if not isinstance(observation['completeness'], str) or observation['completeness'] not in {'complete', 'truncated', 'empty', 'unknown'}:
            raise DiagnosticError('invalid observation completeness')
        if observation['observed_at'] is None and observation['completeness'] != 'unknown':
            raise DiagnosticError('untimed observation must remain unknown')
        if detailed:
            details = observation['container_details']
            _keys(details, ('scope', 'complete', 'truncated', 'unknown', 'not_observed'))
            if details['scope'] != 'application-containers' or sum(
                    count(details[key]) for key in ('complete', 'truncated', 'unknown', 'not_observed')) != observation['count']:
                raise DiagnosticError('container detail coverage disagrees with Pod count')
        if usage_present:
            resource_usage(observation)
    findings = evidence['findings']
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise DiagnosticError('invalid bounded findings')
    for index, finding in enumerate(findings, 1):
        code = finding.get('code') if isinstance(finding, dict) else None
        contextual = isinstance(code, str) and code in CONTAINER_CRITICAL | CONTAINER_HISTORICAL
        _keys(finding, ('id', 'code', 'severity', 'subject', 'allowed_tools') + (('container',) if contextual else ()))
        if finding['id'] != f'f{index:02d}' or not isinstance(code, str) or code not in ALLOWED:
            raise DiagnosticError('invalid finding identity')
        identifier(finding['subject'])
        if finding['severity'] != ('critical' if code in POD_CRITICAL | RUNTIME_CRITICAL else 'warning'):
            raise DiagnosticError('invalid finding severity')
        if finding['allowed_tools'] != _allowed(code, finding['subject']):
            raise DiagnosticError('invalid finding read tools')
        if contextual:
            _validate_container_context(finding)
    if evidence['health'] == 'healthy-observed-scope' and (findings or evidence['omitted_findings']
            or _unknown_container_coverage(evidence['observations'])
            or _unknown_usage_coverage(evidence['observations'])
            or any(x['completeness'] != 'complete' for x in evidence['observations'].values())):
        raise DiagnosticError('incomplete or abnormal evidence cannot assert observed health')
    if evidence['health'] == 'attention-required' and not findings:
        raise DiagnosticError('attention requires a bounded finding')
    observed_attention = any(x['code'] not in UNKNOWN_FINDINGS for x in findings)
    expected_health = ('attention-required' if observed_attention else 'unknown' if findings
        or _unknown_container_coverage(evidence['observations']) or _unknown_usage_coverage(evidence['observations']) or any(
        x['completeness'] != 'complete' for x in evidence['observations'].values()) else 'healthy-observed-scope')
    if evidence['health'] != expected_health:
        raise DiagnosticError('observed health disagrees with bounded findings')
    return evidence


def _validate_container_context(finding):
    context = finding['container']
    _keys(context, ('name', 'observation', 'status', 'ready', 'restarts', 'state'))
    if context['observation'] not in ('current', 'last-termination') or context['status'] not in ('complete', 'truncated', 'unknown'):
        raise DiagnosticError('invalid container evidence context')
    historical = context['observation'] == 'last-termination'
    running = dict(kind='running', reason=None, exit_code=None, started_at=None, finished_at=None)
    observed = container_observation({'container_observation': {
        'status': 'unknown' if context['status'] == 'unknown' else 'complete',
        'scope': 'application-containers', 'containers': [{
            'name': context['name'], 'ready': context['ready'], 'restarts': context['restarts'],
            'state': running if historical else context['state'],
            'last_termination': context['state'] if historical else None}]}})
    if container_code(context['state'], historical=historical) != finding['code'] or observed is None:
        raise DiagnosticError('finding does not match its reported container state')


def _compare_containers(old, new, item):
    """Same Pod UID only; container names do not identify a process or prove recovery."""
    left, right = old.get('container_observation'), new.get('container_observation')
    statuses = [value['status'] if value is not None else 'not-observed' for value in (left, right)]
    item['limitations'].extend(['container-process-identity-not-observed', 'no-recovery-claim'])
    if 'not-observed' in statuses:
        item['limitations'].append('container-details-not-observed')
    incomplete = any(status in ('unknown', 'truncated') for status in statuses)
    if incomplete:
        item['limitations'].append('incomplete-container-details')
    base = {'subject': new['subject'], 'identity': new['uid']}
    if statuses[0] != statuses[1]:
        item['changes'].append(dict(base, kind='container-observation-status-changed',
                                    before=statuses[0], after=statuses[1]))
    if left is None or right is None:
        return incomplete
    left_rows, right_rows = ({row['name']: row for row in value['containers']} for value in (left, right))
    for name in sorted(set(left_rows) | set(right_rows)):
        before, after = left_rows.get(name), right_rows.get(name)
        if before is None or after is None:
            item['changes'].append(dict(base, container_name=name,
                kind='container-first-observed' if before is None else 'container-no-longer-observed'))
        elif before != after:
            fields = [field for field in ('ready', 'restarts', 'state', 'last_termination') if before[field] != after[field]]
            item['changes'].append(dict(base, kind='container-fields-changed', container_name=name,
                                        changed_fields=fields, before=before, after=after))
            if before['restarts'] is not None and after['restarts'] is not None and after['restarts'] < before['restarts']:
                item['limitations'].append('container-restart-count-decreased')
    return incomplete


def validate_triage(response: dict, evidence: dict) -> dict:
    validate_evidence(evidence)
    _keys(response, ('assessment', 'checks', 'root_cause'))
    if response['assessment'] != evidence['health'] or response['root_cause'] != 'undetermined':
        raise DiagnosticError('triage changed observed health or claimed a root cause')
    checks = response['checks']
    if not isinstance(checks, list) or len(checks) > 2:
        raise DiagnosticError('triage exceeds two bounded reads')
    findings = {item['id']: item for item in evidence['findings']}
    seen, covered_pods, covered_runtime = set(), set(), set()
    for check in checks:
        _keys(check, ('tool', 'finding_ids'))
        tool, ids = check['tool'], check['finding_ids']
        if not isinstance(tool, str) or tool not in TOOLS or tool in seen:
            raise DiagnosticError('triage selected an unknown or duplicate read')
        seen.add(tool)
        if (not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids)
                or len(ids) != len(set(ids)) or any(x not in findings for x in ids)):
            raise DiagnosticError('triage requires unique known finding references')
        if any(tool not in findings[x]['allowed_tools'] for x in ids):
            raise DiagnosticError('triage selected a read unrelated to its evidence')
        if tool == TOOLS[1]:
            covered_pods.update(ids)
        if tool == TOOLS[3]:
            covered_runtime.update(ids)
    critical_pods = {key for key, item in findings.items() if item['code'] in POD_CRITICAL}
    critical_runtime = {key for key, item in findings.items() if item['code'] in RUNTIME_CRITICAL}
    if not critical_pods <= covered_pods:
        raise DiagnosticError('triage omitted a critical Pod readiness observation')
    if not critical_runtime <= covered_runtime:
        raise DiagnosticError('triage omitted a critical runtime readiness observation')
    if bool(findings) != bool(checks):
        raise DiagnosticError('triage reads must match the presence of findings')
    return response


def project_observations(snapshots: dict) -> dict:
    """Retain replayable fixed fields, never free-text summaries or messages.

    Accept the four initial reads or any subset of follow-up reads. Missing
    timestamps, truncation flags and UIDs remain unknown, rather than becoming
    evidence of completeness. The one recognized Flux marker is reconstructed
    from a constant so its informational meaning survives evidence replay.
    """
    if not isinstance(snapshots, dict) or not set(snapshots) <= set(TOOLS):
        raise DiagnosticError('invalid retained observation sources')
    result = {}
    for tool, report in snapshots.items():
        source = SOURCES[tool]
        if source == 'platform':
            result[tool] = _platform(report)
            continue
        observation = _observation(source, report)
        projected = {'scope': 'local-experiment', 'observed_at': observation['observed_at']}
        if source == 'pods' and 'resource_usage' in observation:
            projected['resource_usage'] = observation['resource_usage']
        if source == 'doctor':
            projected.update(profile='local-experiment', context='colima-mac-studio-solo', checks=[])
        else:
            projected.update(namespace='shirokuma-dev', truncated=report.get('truncated'))
            projected[source] = []
        key = 'checks' if source == 'doctor' else source
        for item in report[key]:
            if not isinstance(item, dict):
                raise DiagnosticError('invalid retained observation row')
            if source == 'doctor':
                status = item.get('status')
                if not isinstance(status, str) or status not in {'healthy', 'informational', 'degraded'}:
                    raise DiagnosticError('invalid retained doctor status')
                row = {'name': identifier(item.get('name')), 'status': status}
                summary = item.get('summary', '')
                if not isinstance(summary, str):
                    raise DiagnosticError('invalid doctor summary type')
                if row['name'] == 'flux' and status == 'informational' and INTENTIONAL_FLUX_MARKER in summary:
                    row['summary'] = INTENTIONAL_FLUX_MARKER
            elif source == 'pods':
                row = {'name': identifier(item.get('name')), 'uid': _uid(item.get('uid')),
                       'phase': identifier(item.get('phase'))}
                row.update({field: count(item.get(field)) for field in ('ready_containers', 'total_containers', 'restarts')})
                if row['ready_containers'] > row['total_containers']:
                    raise DiagnosticError('invalid retained Pod readiness counts')
                observed = container_observation(item)
                if observed is not None:
                    row['container_observation'] = observed
            else:
                row = {field: identifier(item.get(field)) for field in ('reason', 'type', 'object_kind', 'object_name')}
                row.update(uid=_uid(item.get('uid')), object_uid=_uid(item.get('object_uid')),
                           count=count(item.get('count')), last_observed_at=item.get('last_observed_at'))
                _timestamp(row['last_observed_at'])
            projected[key].append(row)
        result[tool] = projected
    return result


def _rows(source, report):
    """Validate comparison fields only; arbitrary report fields never leave here."""
    values = report['checks' if source == 'doctor' else source]
    result, names = {}, set()
    for item in values:
        if not isinstance(item, dict):
            raise DiagnosticError('invalid observation row')
        name = identifier(item.get('name') if source != 'events' else item.get('object_name'))
        if source == 'doctor':
            if item.get('status') not in {'healthy', 'informational', 'degraded'}:
                raise DiagnosticError('invalid doctor check status')
            uid, row = name, {'subject': name, 'status': item['status']}
        elif source == 'pods':
            uid = _uid(item.get('uid'))
            row = {'subject': name, 'uid': uid, 'phase': identifier(item.get('phase'))}
            row.update({key: count(item.get(key)) for key in ('ready_containers', 'total_containers', 'restarts')})
            if row['ready_containers'] > row['total_containers']:
                raise DiagnosticError('invalid Pod readiness counts')
            if name in names:
                raise DiagnosticError('duplicate Pod name')
            observed = container_observation(item)
            if observed is not None:
                row['container_observation'] = observed
            names.add(name)
        else:
            uid = _uid(item.get('uid'))
            _timestamp(item.get('last_observed_at'))
            row = {'subject': identifier(item.get('object_kind')) + '/' + name, 'uid': uid,
                   'object_uid': _uid(item.get('object_uid')), 'count': count(item.get('count')),
                   'reason': identifier(item.get('reason')), 'type': identifier(item.get('type')),
                   'last_observed_at': item.get('last_observed_at')}
        if uid is None:
            return None
        if uid in result:
            raise DiagnosticError('duplicate observation identity')
        result[uid] = row
    return result


def _compare_resource_usage(before, after, *, reobserved):
    """Compare sample identity/windows only; never infer pressure or a trend."""
    result = {'before_state': before['state'] if before else 'not-observed',
              'after_state': after['state'] if after else 'not-observed' if reobserved else 'not-reobserved',
              'comparison': 'incomparable' if reobserved else 'not-reobserved',
              'limitations': ['name-time-correlation-only', 'no-trend-or-cause-claim']}
    if not reobserved:
        return result
    if before is None or after is None:
        result['limitations'].append('resource-usage-not-observed')
    elif before['state'] != 'observed' or after['state'] != 'observed':
        result['limitations'].append('resource-usage-unknown')
    elif before['identity'] != after['identity']:
        result['limitations'].append('resource-usage-identity-changed')
    else:
        from tools.local_experiment.gateway.pod_usage import timestamp_ns
        left, right = before['sample'], after['sample']
        if timestamp_ns(right['timestamp']) < timestamp_ns(left['timestamp']):
            result['limitations'].append('resource-usage-time-reversed')
        elif (timestamp_ns(left['timestamp']), left['window_ns']) == (timestamp_ns(right['timestamp']), right['window_ns']):
            if all(left[key] == right[key] for key in ('cpu_nanocores', 'memory_working_set_bytes')):
                result['comparison'] = 'same-sample-window'
                result['limitations'].append('cached-sample-not-new-measurement')
            else:
                result['limitations'].append('resource-usage-sample-inconsistent')
        else:
            result['comparison'] = 'different-sample-window'
    return result


def compare_observations(before: dict, after: dict) -> dict:
    """Compare only selected reads; absence is never proof of recovery/deletion."""
    if not isinstance(before, dict) or not isinstance(after, dict) or set(before) != set(TOOLS) or not set(after) <= set(TOOLS):
        raise DiagnosticError('invalid before/after observation sources')
    sources = {}
    for tool, source in SOURCES.items():
        prior = _observation(source, before[tool])
        item = {'status': 'not-reobserved', 'before_observed_at': prior['observed_at'],
                'after_observed_at': None, 'changes': [], 'limitations': ['bounded-snapshots-only']}
        sources[tool] = item
        usage_incomplete = False
        if source == 'pods' and ('resource_usage' in prior or tool in after and 'resource_usage' in after[tool]):
            usage = _compare_resource_usage(prior.get('resource_usage'),
                resource_usage(after[tool]) if tool in after else None, reobserved=tool in after)
            item['resource_usage'] = usage
            usage_incomplete = usage['comparison'] == 'incomparable'
        if tool not in after:
            continue
        current = _observation(source, after[tool])
        item['after_observed_at'] = current['observed_at']
        stamps = [_timestamp(x['observed_at']) for x in (prior, current)]
        if None in stamps or stamps[1] < stamps[0]:
            item.update(status='incomparable')
            item['limitations'].append('missing-or-reversed-observation-time')
            continue
        if source == 'platform':
            left, right = _platform(before[tool]), _platform(after[tool])
            item['limitations'].extend(['component-identity-not-observed', 'no-recovery-claim'])
            item.update(before_ready=left['ready'], after_ready=right['ready'])
            for name in COMPONENTS:
                if left['components'][name] != right['components'][name]:
                    item['changes'].append({'kind': 'runtime-status-changed', 'subject': name,
                                            'before': left['components'][name], 'after': right['components'][name]})
            incomplete = any(x['completeness'] != 'complete' for x in (prior, current))
            if incomplete:
                item['limitations'].append('unknown-runtime-status')
            item['status'] = ('changed' if item['changes'] else 'incomparable' if incomplete else 'no-change-observed')
            continue
        left, right = _rows(source, before[tool]), _rows(source, after[tool])
        if left is None or right is None:
            item.update(status='incomparable')
            item['limitations'].append('missing-object-uid')
            continue
        list_incomplete = any(x['completeness'] != 'complete' for x in (prior, current))
        incomplete = list_incomplete or usage_incomplete
        if list_incomplete:
            item['limitations'].append('incomplete-observation-lists')
        changes = item['changes']
        replacements = set()
        if source == 'pods':
            old_by_name = {row['subject']: uid for uid, row in left.items()}
            for uid, row in right.items():
                old_uid = old_by_name.get(row['subject'])
                if old_uid is not None and old_uid != uid:
                    changes.append({'kind': 'pod-replaced', 'subject': row['subject'],
                                    'before_uid': old_uid, 'after_uid': uid})
                    replacements.update((old_uid, uid))
        for uid in sorted(set(left) | set(right)):
            if uid in replacements:
                continue
            old, new = left.get(uid), right.get(uid)
            if source == 'pods' and old is not None and new is not None:
                incomplete = _compare_containers(old, new, item) or incomplete
                old = {key: value for key, value in old.items() if key != 'container_observation'}
                new = {key: value for key, value in new.items() if key != 'container_observation'}
            if old is None or new is None:
                row = new if old is None else old
                changes.append({'kind': source + ('-first-observed' if old is None else '-no-longer-observed'),
                                'subject': row['subject'], 'identity': uid})
            elif old != new:
                change = {'kind': source + '-changed', 'subject': new['subject'], 'identity': uid,
                          'before': old, 'after': new}
                if source == 'events':
                    if new['count'] < old['count']:
                        item['limitations'].append('event-count-decreased')
                    else:
                        change['count_delta'] = new['count'] - old['count']
                if source == 'pods' and new['restarts'] < old['restarts']:
                    item['limitations'].append('pod-restart-count-decreased')
                changes.append(change)
        item['status'] = ('changed' if changes else 'incomparable' if incomplete else 'no-change-observed')
        item['limitations'] = list(dict.fromkeys(item['limitations']))
    return {'scope': 'selected-bounded-snapshots', 'root_cause': 'undetermined', 'sources': sources}
