"""Pure contracts for bounded evidence, read selection, and snapshot comparison."""
from __future__ import annotations

from datetime import datetime
import re

from diagnostics import DiagnosticError, diagnose, identifier, count

TOOLS = ('shirokuma_doctor', 'k8s_read_pods', 'k8s_read_events')
SOURCES = dict(zip(TOOLS, ('doctor', 'pods', 'events')))
MAX_FINDINGS = 24
INTENTIONAL_FLUX_MARKER = 'root Kustomization/flux-system/flux-system is intentionally suspended for local-experiment'
POD_CRITICAL = {'WORKLOAD_FAILED', 'WORKLOAD_UNAVAILABLE', 'WORKLOAD_NOT_READY'}
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
    'OBSERVATION_UNKNOWN': TOOLS,
}


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


def _observation(source, report):
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
    return {'observed_at': stamp, 'completeness': completeness, 'count': len(values)}


def _allowed(code, subject):
    if code == 'OBSERVATION_UNKNOWN':
        if subject not in SOURCES.values():
            raise DiagnosticError('invalid missing observation source')
        return [next(tool for tool, source in SOURCES.items() if source == subject)]
    if code == 'WARNING_EVENT' and not subject.startswith('Pod/'):
        return [TOOLS[2]]
    return list(ALLOWED[code])


def build_evidence(doctor: dict, pods: dict, events: dict) -> dict:
    """Project no raw summaries, messages, logs, commands, credentials or causes."""
    reports = dict(zip(('doctor', 'pods', 'events'), (doctor, pods, events)))
    observations = {source: _observation(source, report) for source, report in reports.items()}
    baseline = diagnose(doctor, pods, events)
    findings = [{key: item[key] for key in ('code', 'severity', 'subject')}
                for item in baseline['findings'] if item['severity'] in {'warning', 'critical'}]
    if events.get('truncated'):
        findings.append({'code': 'EVENT_OBSERVATION_TRUNCATED', 'severity': 'warning', 'subject': 'events'})
    for source, item in observations.items():
        if item['completeness'] == 'unknown':
            findings.append({'code': 'OBSERVATION_UNKNOWN', 'severity': 'warning', 'subject': source})
    findings.sort(key=lambda item: (item['severity'] != 'critical', item['code'], item['subject']))
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
    if result['health'] == 'healthy-observed-scope' and any(
            item['completeness'] != 'complete' for item in observations.values()):
        result['health'] = 'unknown'
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
    _keys(evidence['observations'], ('doctor', 'pods', 'events'))
    for observation in evidence['observations'].values():
        _keys(observation, ('observed_at', 'completeness', 'count'))
        _timestamp(observation['observed_at'])
        count(observation['count'])
        if not isinstance(observation['completeness'], str) or observation['completeness'] not in {'complete', 'truncated', 'empty', 'unknown'}:
            raise DiagnosticError('invalid observation completeness')
        if observation['observed_at'] is None and observation['completeness'] != 'unknown':
            raise DiagnosticError('untimed observation must remain unknown')
    findings = evidence['findings']
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise DiagnosticError('invalid bounded findings')
    for index, finding in enumerate(findings, 1):
        _keys(finding, ('id', 'code', 'severity', 'subject', 'allowed_tools'))
        code = finding['code']
        if finding['id'] != f'f{index:02d}' or not isinstance(code, str) or code not in ALLOWED:
            raise DiagnosticError('invalid finding identity')
        identifier(finding['subject'])
        if finding['severity'] != ('critical' if code in POD_CRITICAL else 'warning'):
            raise DiagnosticError('invalid finding severity')
        if finding['allowed_tools'] != _allowed(code, finding['subject']):
            raise DiagnosticError('invalid finding read tools')
    if evidence['health'] == 'healthy-observed-scope' and (findings or evidence['omitted_findings']
            or any(x['completeness'] != 'complete' for x in evidence['observations'].values())):
        raise DiagnosticError('incomplete or abnormal evidence cannot assert observed health')
    if evidence['health'] == 'attention-required' and not findings:
        raise DiagnosticError('attention requires a bounded finding')
    observed_attention = any(x['code'] not in {'EVENT_OBSERVATION_TRUNCATED', 'OBSERVATION_UNKNOWN'} for x in findings)
    expected_health = ('attention-required' if observed_attention else 'unknown' if any(
        x['completeness'] != 'complete' for x in evidence['observations'].values()) else 'healthy-observed-scope')
    if evidence['health'] != expected_health:
        raise DiagnosticError('observed health disagrees with bounded findings')
    return evidence


def validate_triage(response: dict, evidence: dict) -> dict:
    validate_evidence(evidence)
    _keys(response, ('assessment', 'checks', 'root_cause'))
    if response['assessment'] != evidence['health'] or response['root_cause'] != 'undetermined':
        raise DiagnosticError('triage changed observed health or claimed a root cause')
    checks = response['checks']
    if not isinstance(checks, list) or len(checks) > 2:
        raise DiagnosticError('triage exceeds two bounded reads')
    findings = {item['id']: item for item in evidence['findings']}
    seen, covered_pods = set(), set()
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
    critical = {key for key, item in findings.items() if item['severity'] == 'critical'}
    if not critical <= covered_pods:
        raise DiagnosticError('triage omitted a critical Pod readiness observation')
    if bool(findings) != bool(checks):
        raise DiagnosticError('triage reads must match the presence of findings')
    return response


def project_observations(snapshots: dict) -> dict:
    """Retain replayable fixed fields, never free-text summaries or messages.

    Accept the three initial reads or any subset of follow-up reads. Missing
    timestamps, truncation flags and UIDs remain unknown, rather than becoming
    evidence of completeness. The one recognized Flux marker is reconstructed
    from a constant so its informational meaning survives evidence replay.
    """
    if not isinstance(snapshots, dict) or not set(snapshots) <= set(TOOLS):
        raise DiagnosticError('invalid retained observation sources')
    result = {}
    for tool, report in snapshots.items():
        source = SOURCES[tool]
        observation = _observation(source, report)
        projected = {'scope': 'local-experiment', 'observed_at': observation['observed_at']}
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
        if tool not in after:
            continue
        current = _observation(source, after[tool])
        item['after_observed_at'] = current['observed_at']
        stamps = [_timestamp(x['observed_at']) for x in (prior, current)]
        if None in stamps or stamps[1] < stamps[0]:
            item.update(status='incomparable')
            item['limitations'].append('missing-or-reversed-observation-time')
            continue
        left, right = _rows(source, before[tool]), _rows(source, after[tool])
        if left is None or right is None:
            item.update(status='incomparable')
            item['limitations'].append('missing-object-uid')
            continue
        incomplete = any(x['completeness'] != 'complete' for x in (prior, current))
        if incomplete:
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
    return {'scope': 'selected-bounded-snapshots', 'root_cause': 'undetermined', 'sources': sources}
