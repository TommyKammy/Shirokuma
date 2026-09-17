"""Bounded Query Doctor evidence and fixed follow-up read selection contracts."""
from __future__ import annotations

import copy
from datetime import datetime
import math
import re

CASES = ('projection', 'filtered')
TOOLS = ('fixture_stats', 'own_query_history')
ESTIMATE_FIELDS = ('rows', 'bytes', 'cpu_cost', 'memory_cost', 'network_cost')
TIMING_FIELDS = ('queued_ms', 'analysis_ms', 'planning_ms', 'created_to_end_ms', 'started_to_end_ms')
STATES = ('finished', 'failed', 'nonterminal')


class ContractError(ValueError):
    pass


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError('unexpected Query Doctor contract fields')


def count(value, maximum=10000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ContractError('invalid bounded Query Doctor count')
    return value


def findings_for(cases: list, history: dict) -> list:
    findings = []
    for case in cases:
        if not case['estimates_complete']:
            findings.append({'code': 'ESTIMATES_UNKNOWN', 'subject': case['id'], 'tool': 'fixture_stats'})
    if history['completeness'] != 'complete':
        findings.append({'code': 'HISTORY_INCOMPLETE', 'subject': 'captured_queries', 'tool': 'own_query_history'})
    if history['state_counts']['failed']:
        findings.append({'code': 'HISTORY_FAILED', 'subject': 'captured_queries', 'tool': 'own_query_history'})
    if history['state_counts']['nonterminal']:
        findings.append({'code': 'HISTORY_NONTERMINAL', 'subject': 'captured_queries', 'tool': 'own_query_history'})
    if not history['durations_complete'] or history['timing_unknown_count']:
        findings.append({'code': 'HISTORY_TIMING_UNKNOWN', 'subject': 'captured_queries', 'tool': 'own_query_history'})
    return [dict(finding, id=f'f{index:02d}') for index, finding in enumerate(findings, 1)]


def build_evidence(cases: list, history: dict) -> dict:
    value = {'schema_version': 1, 'scope': 'fixed-two-row-fixture', 'cases': copy.deepcopy(cases),
             'history': copy.deepcopy(history), 'findings': findings_for(cases, history)}
    return validate_evidence(value)


def validate_evidence(value: dict) -> dict:
    keys(value, ('schema_version', 'scope', 'cases', 'history', 'findings'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['scope'] != 'fixed-two-row-fixture':
        raise ContractError('unknown Query Doctor scope or schema')
    cases = value['cases']
    if type(cases) is not list or len(cases) != 2:
        raise ContractError('Query Doctor requires the two fixed query cases')
    for case, case_id, rows in zip(cases, CASES, (2, 1)):
        keys(case, ('id', 'row_count', 'fragment_count', 'node_count', 'filter_present',
                    'estimate_slots', 'estimate_empty_nodes', 'estimate_unknown_count', 'estimate_zero_count', 'estimates_complete'))
        if case['id'] != case_id or count(case['row_count'], 2) != rows:
            raise ContractError('query result does not match the fixed fixture case')
        if not 1 <= count(case['fragment_count'], 64) or not 1 <= count(case['node_count'], 128):
            raise ContractError('query plan is missing bounded fragments or nodes')
        slots = count(case['estimate_slots'], 128)
        empty_nodes = count(case['estimate_empty_nodes'], case['node_count'])
        unknown, zero = count(case['estimate_unknown_count'], slots * 5), count(case['estimate_zero_count'], slots * 5)
        if unknown + zero > slots * 5:
            raise ContractError('query estimate counts exceed observed slots')
        if (type(case['filter_present']) is not bool or type(case['estimates_complete']) is not bool
                or case['estimates_complete'] != (slots > 0 and empty_nodes == 0 and unknown == 0)):
            raise ContractError('query estimate completeness disagrees with known and missing values')
    history = value['history']
    keys(history, ('completeness', 'observed_queries', 'missing_queries', 'state_counts',
                   'timing_unknown_count', 'timing_zero_count', 'durations_complete'))
    observed, missing = count(history['observed_queries'], 4), count(history['missing_queries'], 4)
    if observed + missing != 4:
        raise ContractError('history must account for exactly the four captured queries')
    completeness = 'complete' if missing == 0 else 'missing' if observed == 0 else 'partial'
    if history['completeness'] != completeness:
        raise ContractError('history completeness disagrees with missing captured queries')
    keys(history['state_counts'], STATES)
    if sum(count(item, 4) for item in history['state_counts'].values()) != observed:
        raise ContractError('history state counts disagree with observed queries')
    unknown = count(history['timing_unknown_count'], observed * len(TIMING_FIELDS))
    zero = count(history['timing_zero_count'], observed * len(TIMING_FIELDS))
    if unknown + zero > observed * len(TIMING_FIELDS) or type(history['durations_complete']) is not bool:
        raise ContractError('history timing counts or completeness are invalid')
    if history['durations_complete'] and (missing or history['state_counts']['nonterminal']):
        raise ContractError('missing history timings cannot claim complete durations')
    if value['findings'] != findings_for(cases, history):
        raise ContractError('Query Doctor findings differ from observed unknowns and states')
    return copy.deepcopy(value)


def number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ContractError('numeric Query Doctor observation must be nonnegative and finite or null')
    return value


def query_id(value):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9_]{1,128}', value):
        raise ContractError('invalid captured query identifier')
    return value


def timestamp(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', value):
        raise ContractError('Query Doctor timestamp must be a bounded UTC observation')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ContractError('invalid Query Doctor observation time') from None


def captured_queries(observation: dict) -> dict:
    captured = {}
    for case, case_id in zip(observation['cases'], CASES):
        if case.get('id') != case_id:
            raise ContractError('query cases differ from the fixed fixture')
        for kind in ('select', 'explain'):
            identity = query_id(case[kind]['query_id'])
            if identity in captured:
                raise ContractError('captured query identities are duplicated')
            captured[identity] = {'case_id': case_id, 'kind': kind}
    if len(captured) != 4:
        raise ContractError('the four fixed query identities are required')
    return captured


def history_summary(value: dict, captured: dict) -> dict:
    keys(value, ('query_id', 'observed_at', 'completeness', 'missing_query_ids', 'entries', 'durations_complete'))
    query_id(value['query_id'])
    timestamp(value['observed_at'])
    entries = value['entries']
    if type(entries) is not list or len(entries) > 4:
        raise ContractError('query history exceeds the four captured queries')
    seen, timings, states, durations = set(), [], dict.fromkeys(STATES, 0), []
    for entry in entries:
        keys(entry, ('query_id', 'case_id', 'kind', 'state', *TIMING_FIELDS,
                     'created_at', 'started_at', 'ended_at', 'error_type', 'error_code'))
        identity = query_id(entry['query_id'])
        if (identity not in captured or identity in seen
                or {'case_id': entry['case_id'], 'kind': entry['kind']} != captured[identity]):
            raise ContractError('history contains a foreign, repeated or misclassified query')
        seen.add(identity)
        state = entry['state']
        if type(state) is not str or state not in {'FINISHED', 'FAILED', 'QUEUED', 'WAITING_FOR_RESOURCES',
                                                  'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING'}:
            raise ContractError('unknown query history state')
        states['finished' if state == 'FINISHED' else 'failed' if state == 'FAILED' else 'nonterminal'] += 1
        for field in TIMING_FIELDS:
            item = number(entry[field])
            if item is not None and type(item) is not int:
                raise ContractError('query durations must be integral milliseconds or null')
            timings.append(item)
        dates = [timestamp(entry[field], nullable=True) for field in ('created_at', 'started_at', 'ended_at')]
        if any(left is not None and right is not None and left > right
               for left, right in ((dates[0], dates[1]), (dates[0], dates[2]), (dates[1], dates[2]))):
            raise ContractError('query history timestamps are reversed')
        for field in ('error_type', 'error_code'):
            item = entry[field]
            if item is not None and (type(item) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,127}', item)):
                raise ContractError('query error classification is not a safe enum')
        duration_fields = [entry['created_to_end_ms'], entry['started_to_end_ms']]
        if state not in {'FINISHED', 'FAILED'} and any(item is not None for item in duration_fields):
            raise ContractError('nonterminal query cannot claim terminal durations')
        for begin, recorded in zip(dates[:2], duration_fields):
            expected = int((dates[2] - begin).total_seconds() * 1000) if begin is not None and dates[2] is not None and state in {'FINISHED', 'FAILED'} else None
            if recorded != expected:
                raise ContractError('query duration does not match its retained timestamps')
        durations.extend(duration_fields)
    missing = sorted(set(captured) - seen)
    if value['missing_query_ids'] != missing:
        raise ContractError('history missing identities differ from the captured set')
    complete = not missing and all(item is not None for item in durations)
    if type(value['durations_complete']) is not bool or value['durations_complete'] != complete:
        raise ContractError('history duration completeness disagrees with observations')
    return {'completeness': value['completeness'], 'observed_queries': len(entries), 'missing_queries': len(missing),
            'state_counts': states, 'timing_unknown_count': sum(item is None for item in timings),
            'timing_zero_count': sum(item == 0 for item in timings if item is not None),
            'durations_complete': complete}


def project_observation(value: dict) -> dict:
    keys(value, ('schema_version', 'run_token', 'observed_at', 'cases', 'history', 'performance_gain'))
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['run_token']) is not str or not re.fullmatch('[0-9a-f]{32}', value['run_token'])
            or value['performance_gain'] != 'unmeasured'):
        raise ContractError('query observation is outside the fixed local scope')
    timestamp(value['observed_at'])
    if type(value['cases']) is not list or len(value['cases']) != 2:
        raise ContractError('query observation requires two fixed cases')
    captured = captured_queries(value)
    cases = []
    for raw, case_id, rows in zip(value['cases'], CASES, (2, 1)):
        keys(raw, ('id', 'select', 'explain'))
        keys(raw['select'], ('query_id', 'row_count', 'matched'))
        keys(raw['explain'], ('query_id', 'plan'))
        if raw['select']['matched'] is not True or type(raw['select']['row_count']) is not int or raw['select']['row_count'] != rows:
            raise ContractError('query result was not independently verified')
        plan = raw['explain']['plan']
        keys(plan, ('format', 'fragment_count', 'nodes', 'estimates_complete'))
        if plan['format'] != 'trino-483-distributed-json' or type(plan['nodes']) is not list or len(plan['nodes']) != 2:
            raise ContractError('unsupported fixed query plan projection')
        estimates, empty = [], 0
        kinds = ('Output', 'TableScan' if case_id == 'projection' else 'ScanFilter')
        for index, (node, kind) in enumerate(zip(plan['nodes'], kinds), 1):
            keys(node, ('id', 'kind', 'columns', 'filter_present', 'estimates'))
            if (node['id'] != f'p{index:02}' or node['kind'] != kind
                    or node['columns'] != [{'name': 'id', 'type': 'bigint'}]
                    or type(node['filter_present']) is not bool or node['filter_present'] != (kind == 'ScanFilter')
                    or type(node['estimates']) is not list or len(node['estimates']) > 4):
                raise ContractError('query plan operator or fixed columns differ')
            empty += not node['estimates']
            for estimate in node['estimates']:
                keys(estimate, ESTIMATE_FIELDS)
                estimates.extend(number(estimate[field]) for field in ESTIMATE_FIELDS)
        cases.append({'id': case_id, 'row_count': rows, 'fragment_count': plan['fragment_count'],
            'node_count': len(plan['nodes']), 'filter_present': any(node['filter_present'] for node in plan['nodes']),
            'estimate_slots': len(estimates) // len(ESTIMATE_FIELDS), 'estimate_empty_nodes': empty,
            'estimate_unknown_count': sum(item is None for item in estimates),
            'estimate_zero_count': sum(item == 0 for item in estimates if item is not None),
            'estimates_complete': plan['estimates_complete']})
    return build_evidence(cases, history_summary(value['history'], captured))


def followup_summary(observation: dict, tool: str, value: dict) -> dict:
    evidence = project_observation(observation)
    captured = captured_queries(observation)
    if tool == 'own_query_history':
        summary = history_summary(value, captured)
        # Missing or failed history remains an observation, not proof of recovery.
        build_evidence(evidence['cases'], summary)
    elif tool == 'fixture_stats':
        keys(value, ('query_id', 'observed_at', 'table', 'row_count_estimate', 'columns', 'estimates_complete'))
        query_id(value['query_id'])
        timestamp(value['observed_at'])
        if value['table'] != 'fixture_v1' or type(value['columns']) is not list or len(value['columns']) != 2:
            raise ContractError('statistics must refer only to the retained fixture')
        values = [number(value['row_count_estimate'])]
        for column, name in zip(value['columns'], ('id', 'name')):
            keys(column, ('name', 'data_size', 'distinct_values_count', 'nulls_fraction'))
            if column['name'] != name:
                raise ContractError('statistics columns differ from the fixed fixture')
            for field in ('data_size', 'distinct_values_count', 'nulls_fraction'):
                values.append(number(column[field]))
            if column['nulls_fraction'] is not None and column['nulls_fraction'] > 1:
                raise ContractError('statistics null fraction exceeds one')
        complete = all(item is not None for item in values)
        if type(value['estimates_complete']) is not bool or value['estimates_complete'] != complete:
            raise ContractError('statistics completeness disagrees with missing numeric estimates')
        summary = {'table': 'fixture_v1', 'row_count_estimate': value['row_count_estimate'],
                   'unknown_count': sum(item is None for item in values), 'estimates_complete': complete}
    else:
        raise ContractError('unknown fixed Query Doctor follow-up')
    if timestamp(value['observed_at']) < timestamp(observation['observed_at']):
        raise ContractError('follow-up observation precedes initial collection')
    return summary


def assess_followups(observation: dict, plan: dict, followups: dict) -> dict:
    evidence = project_observation(observation)
    plan = validate_plan(plan, evidence)
    if type(followups) is not dict or set(followups) != {check['tool'] for check in plan['checks']}:
        raise ContractError('follow-up observations differ from the validated read selection')
    summaries = {tool: followup_summary(observation, tool, value) for tool, value in followups.items()}
    return {'scope': 'fixed-two-row-fixture-only', 'root_cause': 'undetermined', 'performance_gain': 'unmeasured',
            'select_row_counts': {'projection': 2, 'filtered': 1}, 'followups': summaries,
            'limitations': ['EXPLAIN and SHOW STATS are estimates, not measured resource consumption.',
                           'Different fixed SELECTs are not a before/after performance experiment.',
                           'Coordinator history and repeated reads are bounded snapshots.']}


def expected_plan(evidence: dict) -> dict:
    evidence = validate_evidence(evidence)
    checks = [{'tool': tool, 'finding_ids': [finding['id'] for finding in evidence['findings'] if finding['tool'] == tool]}
              for tool in TOOLS]
    return {'schema_version': 1, 'checks': [check for check in checks if check['finding_ids']],
            'root_cause': 'undetermined', 'performance_gain': 'unmeasured'}


def validate_plan(value: dict, evidence: dict) -> dict:
    expected = expected_plan(evidence)
    keys(value, ('schema_version', 'checks', 'root_cause', 'performance_gain'))
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['root_cause'] != 'undetermined' or value['performance_gain'] != 'unmeasured'):
        raise ContractError('Query Doctor cannot infer a cause or performance improvement')
    checks = value['checks']
    if type(checks) is not list or len(checks) > 2:
        raise ContractError('Query Doctor permits at most two fixed follow-up reads')
    allowed = {check['tool']: set(check['finding_ids']) for check in expected['checks']}
    seen = set()
    for check in checks:
        keys(check, ('tool', 'finding_ids'))
        tool, refs = check['tool'], check['finding_ids']
        if type(tool) is not str or tool not in allowed or tool in seen:
            raise ContractError('Query Doctor selected an unrelated or duplicate read')
        if (type(refs) is not list or any(type(ref) is not str for ref in refs)
                or len(set(refs)) != len(refs) or set(refs) != allowed[tool]):
            raise ContractError('Query Doctor read must cite all corresponding observed findings')
        seen.add(tool)
    if seen != set(allowed):
        raise ContractError('Query Doctor omitted a required bounded observation')
    return expected


def model_cases() -> list[dict]:
    complete_cases = [{'id': case_id, 'row_count': rows, 'fragment_count': 1, 'node_count': 2,
        'filter_present': case_id == 'filtered', 'estimate_slots': 2, 'estimate_unknown_count': 0,
        'estimate_empty_nodes': 0, 'estimate_zero_count': 0, 'estimates_complete': True} for case_id, rows in zip(CASES, (2, 1))]
    complete_history = {'completeness': 'complete', 'observed_queries': 4, 'missing_queries': 0,
        'state_counts': {'finished': 4, 'failed': 0, 'nonterminal': 0},
        'timing_unknown_count': 0, 'timing_zero_count': 0, 'durations_complete': True}
    result = []
    for case_id in ('complete_observations', 'projection_estimates_unknown', 'both_estimates_unknown',
                    'history_missing', 'estimates_and_history_unknown', 'zero_is_known',
                    'failed_captured_query', 'history_timing_unknown'):
        cases, history = copy.deepcopy(complete_cases), copy.deepcopy(complete_history)
        if case_id in {'projection_estimates_unknown', 'both_estimates_unknown', 'estimates_and_history_unknown'}:
            cases[0].update(estimate_unknown_count=2, estimates_complete=False)
        if case_id == 'both_estimates_unknown':
            cases[1].update(estimate_slots=0, estimate_empty_nodes=2, estimate_unknown_count=0, estimates_complete=False)
        if case_id in {'history_missing', 'estimates_and_history_unknown'}:
            history.update(completeness='partial', observed_queries=3, missing_queries=1,
                           state_counts={'finished': 3, 'failed': 0, 'nonterminal': 0}, durations_complete=False)
        if case_id == 'zero_is_known':
            for case in cases:
                case['estimate_zero_count'] = 10
            history['timing_zero_count'] = 20
        if case_id == 'failed_captured_query':
            history['state_counts'] = {'finished': 3, 'failed': 1, 'nonterminal': 0}
        if case_id == 'history_timing_unknown':
            history.update(timing_unknown_count=2, durations_complete=False)
        evidence = build_evidence(cases, history)
        result.append({'case_id': case_id, 'evidence': evidence, 'expected': expected_plan(evidence)})
    return result
