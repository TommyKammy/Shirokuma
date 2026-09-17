"""Advisory policy classifications; model output never grants authorization."""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from policy import gatekeeper as CORE

NATIVE_VERIFY = 'V_NATIVE_SERVER_DRY_RUN'
BOUNDARY_VERIFY = 'V_HOST_BOUNDARY_CLASSIFICATION'
CONTAINER_FIELDS = ('privileged', 'cpu_request_present', 'memory_request_present',
                    'cpu_limit_present', 'memory_limit_present', 'nonzero_host_port', 'image_digest_syntax')
SOURCE_FIELDS = ('kyverno_source', 'native_manifest', 'local_authorization_adr')


class ContractError(ValueError):
    pass


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError('unexpected advisory policy contract fields')


def identifier(value, pattern):
    if type(value) is not str or not re.fullmatch(pattern, value):
        raise ContractError('invalid bounded policy identifier')
    return value


def native_candidate(value):
    keys(value, ('id', 'kind', 'namespace_scope', 'operation', 'features'))
    identifier(value['id'], r'c(?:0[1-9]|1[0-5])')
    if value['namespace_scope'] not in ('protected', 'outside') or value['operation'] != 'server_dry_run':
        raise ContractError('native policy candidate is outside the fixed dry-run scope')
    features = value['features']
    if value['kind'] == 'Pod':
        keys(features, ('containers', 'host_network', 'host_ipc', 'host_pid', 'host_path'))
        if type(features['containers']) is not list or len(features['containers']) != 1:
            raise ContractError('fixed policy candidate requires one projected container')
        keys(features['containers'][0], CONTAINER_FIELDS)
        flags = [features[field] for field in ('host_network', 'host_ipc', 'host_pid', 'host_path')]
        flags += list(features['containers'][0].values())
    elif value['kind'] == 'Service':
        keys(features, ('service_type', 'external_ips'))
        if features['service_type'] not in ('ClusterIP', 'NodePort', 'LoadBalancer'):
            raise ContractError('unknown service type in fixed policy candidate')
        flags = [features['external_ips']]
    elif value['kind'] == 'Ingress':
        keys(features, ('ingress_present',))
        if features['ingress_present'] is not True:
            raise ContractError('Ingress candidate must report its presence')
        flags = []
    else:
        raise ContractError('unknown native policy candidate kind')
    if any(type(flag) is not bool for flag in flags):
        raise ContractError('policy feature flags must be booleans')
    return copy.deepcopy(value)


def boundary_candidate(value):
    keys(value, ('id', 'action', 'scope', 'operation'))
    identifier(value['id'], r'a(?:0[1-9]|10)')
    allowed = {item['action'] for item in CORE.boundary_inputs()}
    if (value['action'] not in allowed or value['scope'] != 'shirokuma_local_experiment'
            or value['operation'] != 'classify_only'):
        raise ContractError('boundary candidate must be one fixed nonexecuting classification')
    return copy.deepcopy(value)


def build_evidence(candidates: list, scope: str) -> dict:
    native = scope == 'native-baseline'
    value = {'schema_version': 1, 'authority': 'advisory_only', 'scope': scope,
        'rules': copy.deepcopy(CORE.RULES if native else CORE.BOUNDARY_RULES),
        'limits': list(CORE.LIMITS), 'scope_limit_ids': [CORE.OUTSIDE] if native else [],
        'verification_ids': [NATIVE_VERIFY if native else BOUNDARY_VERIFY], 'candidates': copy.deepcopy(candidates)}
    return validate_evidence(value)


def validate_evidence(value: dict) -> dict:
    keys(value, ('schema_version', 'authority', 'scope', 'rules', 'limits', 'scope_limit_ids', 'verification_ids', 'candidates'))
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['authority'] != 'advisory_only' or value['scope'] not in ('native-baseline', 'authorization-boundary')):
        raise ContractError('policy evidence must remain advisory and in the fixed local scope')
    native = value['scope'] == 'native-baseline'
    if (value['rules'] != (CORE.RULES if native else CORE.BOUNDARY_RULES) or value['limits'] != CORE.LIMITS
            or value['scope_limit_ids'] != ([CORE.OUTSIDE] if native else [])
            or value['verification_ids'] != [NATIVE_VERIFY if native else BOUNDARY_VERIFY]):
        raise ContractError('policy evidence must carry the fixed rule and verification identifiers')
    candidates = value['candidates']
    if type(candidates) is not list or not 1 <= len(candidates) <= (5 if native else 10):
        raise ContractError('policy candidate batch exceeds the bounded inference size')
    for candidate in candidates:
        (native_candidate if native else boundary_candidate)(candidate)
    if len({item['id'] for item in candidates}) != len(candidates):
        raise ContractError('policy candidate identities are duplicated')
    return copy.deepcopy(value)


def validate_observation(value: dict) -> dict:
    keys(value, ('schema_version', 'observed_at', 'authority', 'baseline_ready', 'protected_namespace', 'policy_sources',
                 'rules', 'boundary_rules', 'limits', 'scope_limit_ids', 'candidates', 'boundary_candidates'))
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['authority'] != 'advisory_only' or value['baseline_ready'] is not True
            or value['protected_namespace'] != 'shirokuma-dev'):
        raise ContractError('native baseline observation is not ready in the protected namespace')
    identifier(value['observed_at'], r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z')
    try:
        datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
    except ValueError:
        raise ContractError('invalid bounded policy observation timestamp') from None
    keys(value['policy_sources'], SOURCE_FIELDS)
    for digest in value['policy_sources'].values():
        identifier(digest, '[0-9a-f]{64}')
    if (value['rules'] != CORE.RULES or value['boundary_rules'] != CORE.BOUNDARY_RULES
            or value['limits'] != CORE.LIMITS or value['scope_limit_ids'] != [CORE.OUTSIDE]):
        raise ContractError('policy observation rule definitions or limits differ')
    for name, prefix, count, validator in [('candidates', 'c', 15, native_candidate),
                                         ('boundary_candidates', 'a', 10, boundary_candidate)]:
        candidates = value[name]
        if type(candidates) is not list or len(candidates) != count:
            raise ContractError('policy observation has an incomplete fixed corpus')
        for index, candidate in enumerate(candidates, 1):
            validator(candidate)
            if candidate['id'] != f'{prefix}{index:02}':
                raise ContractError('policy observation candidate order or identity differs')
    return copy.deepcopy(value)


def batches(observation: dict) -> list[dict]:
    value = validate_observation(observation)
    return [build_evidence(value['candidates'][start:start + 5], 'native-baseline') for start in range(0, 15, 5)] + [
        build_evidence(value['boundary_candidates'], 'authorization-boundary')]


def expected_plan(evidence: dict, truth: dict) -> dict:
    evidence = validate_evidence(evidence)
    keys(truth, ('candidates', 'boundaries'))
    native = evidence['scope'] == 'native-baseline'
    answer = truth['candidates' if native else 'boundaries']
    if type(answer) is not list:
        raise ContractError('host policy truth is unavailable')
    by_id = {}
    for item in answer:
        keys(item, ('id', 'violation_ids', 'scope_limit_ids') if native else ('id', 'boundary_ids'))
        if item['id'] in by_id:
            raise ContractError('host policy truth repeats a candidate')
        by_id[item['id']] = item
    classifications = []
    for candidate in evidence['candidates']:
        if candidate['id'] not in by_id:
            raise ContractError('host policy truth omits a candidate')
        item = by_id[candidate['id']]
        classifications.append({'id': candidate['id'], 'violation_ids': list(item['violation_ids']) if native else [],
                                'limit_ids': list(item['scope_limit_ids'] if native else item['boundary_ids'])})
    return validate_plan({'schema_version': 1, 'classifications': classifications,
                          'verification_ids': evidence['verification_ids']}, evidence)


def validate_plan(value: dict, evidence: dict, truth: dict | None = None) -> dict:
    evidence = validate_evidence(evidence)
    keys(value, ('schema_version', 'classifications', 'verification_ids'))
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['verification_ids'] != evidence['verification_ids']):
        raise ContractError('policy output cannot choose a different verification or authorization')
    items = value['classifications']
    if type(items) is not list or len(items) != len(evidence['candidates']):
        raise ContractError('policy output must classify every supplied candidate exactly once')
    native = evidence['scope'] == 'native-baseline'
    allowed_violations = {rule['id'] for rule in CORE.RULES} if native else set()
    allowed_limits = {CORE.OUTSIDE} if native else {rule['id'] for rule in CORE.BOUNDARY_RULES}
    mapped = {}
    for item in items:
        keys(item, ('id', 'violation_ids', 'limit_ids'))
        if type(item['id']) is not str or item['id'] in mapped:
            raise ContractError('policy classification repeats an identity')
        for field, allowed in [('violation_ids', allowed_violations), ('limit_ids', allowed_limits)]:
            refs = item[field]
            if (type(refs) is not list or any(type(ref) is not str or ref not in allowed for ref in refs)
                    or len(set(refs)) != len(refs)):
                raise ContractError('policy output contains unknown or repeated rule identifiers')
        mapped[item['id']] = {'id': item['id'], 'violation_ids': sorted(item['violation_ids']), 'limit_ids': sorted(item['limit_ids'])}
    if set(mapped) != {item['id'] for item in evidence['candidates']}:
        raise ContractError('policy output selects an unobserved candidate')
    plan = {'schema_version': 1, 'classifications': [mapped[item['id']] for item in evidence['candidates']],
            'verification_ids': list(evidence['verification_ids'])}
    if truth is not None and plan != expected_plan(evidence, truth):
        raise ContractError('model policy classification disagrees with independent host expectations')
    return plan


def verify_receipt(value: dict, observation: dict, truth: dict) -> dict:
    observed = validate_observation(observation)
    required = {'schema_version', 'observed_at', 'server_dry_run', 'persisted_workloads', 'authority_granted',
                'boundary_actions_executed', 'started', 'results', 'limits', 'result', 'passed_cases',
                'namespace_cases', 'outside_namespace_controls', 'policy_sources', 'pending'}
    keys(value, required)
    if (type(value['schema_version']) is not int or value['schema_version'] != 1 or value['result'] != 'passed'
            or value['server_dry_run'] is not True or value['started'] is not True or value['authority_granted'] is not False
            or value['limits'] != CORE.LIMITS or value['policy_sources'] != observed['policy_sources']
            or value['pending'] is not None):
        raise ContractError('policy verification must remain a successful fixed server dry-run')
    for field, expected in [('persisted_workloads', 0), ('boundary_actions_executed', 0), ('passed_cases', 15),
                            ('namespace_cases', 14), ('outside_namespace_controls', 1)]:
        if type(value[field]) is not int or value[field] != expected:
            raise ContractError('policy verification counts exceed or omit the fixed scope')
    if type(value['results']) is not list or len(value['results']) != 15:
        raise ContractError('policy verification omits fixed candidate outcomes')
    expected = {item['id']: item for batch in batches(observed)[:3]
                for item in expected_plan(batch, truth)['classifications']}
    for index, row in enumerate(value['results'], 1):
        keys(row, ('candidate_id', 'admission', 'matched_host_expectation', 'server_dry_run',
                   'violation_ids', 'scope_limit_ids', 'input_sha256'))
        cid = f'c{index:02}'
        answer = expected[cid]
        if (row['candidate_id'] != cid or row['matched_host_expectation'] is not True or row['server_dry_run'] is not True
                or row['violation_ids'] != answer['violation_ids'] or row['scope_limit_ids'] != answer['limit_ids']
                or row['admission'] != ('denied' if answer['violation_ids'] else 'allowed')):
            raise ContractError('native admission outcome differs from fixed host expectations')
        identifier(row['input_sha256'], '[0-9a-f]{64}')
    return copy.deepcopy(value)


def model_cases() -> list[dict]:
    candidates = CORE.candidate_inputs('shirokuma.local/trino@sha256:' + '0' * 64)
    boundaries, truth = CORE.boundary_inputs(), CORE.host_expectations()
    cases = [
        ('native_baseline', candidates[:2], 'native-baseline'),
        ('native_host_controls', candidates[2:7], 'native-baseline'),
        ('native_container_controls', candidates[7:10], 'native-baseline'),
        ('native_exposure', candidates[10:14], 'native-baseline'),
        ('native_namespace_control', [candidates[9], candidates[14]], 'native-baseline'),
        ('boundary_local_scope', boundaries[:2], 'authorization-boundary'),
        ('boundary_data_and_external', boundaries[2:6], 'authorization-boundary'),
        ('boundary_environment_scope', boundaries[6:], 'authorization-boundary'),
    ]
    result = []
    for case_id, inputs, scope in cases:
        evidence = build_evidence(inputs, scope)
        result.append({'case_id': case_id, 'evidence': evidence, 'expected': expected_plan(evidence, truth)})
    return result
