"""Typed selection of missing fields on two verified historical catalog entities."""
from __future__ import annotations

import copy

TARGETS = ('stg', 'mart')
FIELDS = ('description', 'owner', 'lineage')
EVIDENCE_IDS = ('e01', 'e02', 'e03', 'e04', 'e05', 'e06')


class ContractError(ValueError):
    pass


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError('unexpected catalog contract fields')


def validate_evidence(value: dict) -> dict:
    keys(value, ('schema_version', 'scope', 'verified', 'targets', 'evidence_ids'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ContractError('unknown catalog evidence version')
    if type(value['scope']) is not str or value['scope'] not in {'generated-history', 'out-of-scope'}:
        raise ContractError('invalid catalog evidence scope')
    if type(value['verified']) is not bool or value['evidence_ids'] != list(EVIDENCE_IDS):
        raise ContractError('catalog evidence must identify the six fixed proof groups')
    targets = value['targets']
    if type(targets) is not list or len(targets) != 2:
        raise ContractError('catalog evidence requires exactly two fixed targets')
    for target, target_id in zip(targets, TARGETS):
        keys(target, ('id', 'missing_fields'))
        fields = target['missing_fields']
        if (target['id'] != target_id or type(fields) is not list
                or any(type(field) is not str or field not in FIELDS for field in fields)
                or fields != [field for field in FIELDS if field in fields]):
            raise ContractError('invalid catalog target or missing-field set')
    return copy.deepcopy(value)


def build_evidence(missing: dict) -> dict:
    """Only host-verified observation flags may be projected for live inference."""
    keys(missing, TARGETS)
    targets = []
    for target_id in TARGETS:
        keys(missing[target_id], FIELDS)
        if any(type(value) is not bool for value in missing[target_id].values()):
            raise ContractError('catalog missing-field flags must be explicit booleans')
        targets.append({'id': target_id, 'missing_fields': [field for field in FIELDS if missing[target_id][field]]})
    return validate_evidence({'schema_version': 1, 'scope': 'generated-history', 'verified': True,
                              'targets': targets, 'evidence_ids': list(EVIDENCE_IDS)})


def expected_plan(evidence: dict) -> dict:
    """Host truth used to reject omission, fabrication, rewrites and unsupported scope."""
    evidence = validate_evidence(evidence)
    eligible = evidence['verified'] and evidence['scope'] == 'generated-history'
    actions = ([{'target': target['id'], 'field': field} for target in evidence['targets']
                for field in target['missing_fields']] if eligible else [])
    return {'schema_version': 1, 'decision': 'curate' if actions else 'noop' if eligible else 'unsupported',
            'actions': actions, 'evidence_ids': list(EVIDENCE_IDS)}


def validate_plan(value: dict, evidence: dict, *, executable: bool = False) -> dict:
    expected = expected_plan(evidence)
    keys(value, ('schema_version', 'decision', 'actions', 'evidence_ids'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ContractError('unknown catalog plan version')
    if type(value['decision']) is not str or value['decision'] != expected['decision']:
        raise ContractError('catalog decision differs from verified scope and missing fields')
    actions = value['actions']
    if type(actions) is not list or len(actions) > len(TARGETS) * len(FIELDS):
        raise ContractError('catalog plan exceeds the bounded field set')
    selected = []
    for action in actions:
        keys(action, ('target', 'field'))
        if type(action['target']) is not str or type(action['field']) is not str:
            raise ContractError('catalog action requires fixed identifiers')
        if action['target'] not in TARGETS or action['field'] not in FIELDS:
            raise ContractError('catalog action exceeds the two fixed entities')
        selected.append((action['target'], action['field']))
    wanted = [(action['target'], action['field']) for action in expected['actions']]
    if len(set(selected)) != len(selected) or set(selected) != set(wanted):
        raise ContractError('catalog actions must exactly match all missing fields')
    refs = value['evidence_ids']
    if (type(refs) is not list or any(type(ref) is not str for ref in refs)
            or len(refs) != len(EVIDENCE_IDS) or set(refs) != set(EVIDENCE_IDS)):
        raise ContractError('catalog plan must cite each fixed evidence group exactly once')
    if type(executable) is not bool or executable and expected['decision'] == 'unsupported':
        raise ContractError('unsupported catalog evidence cannot be curated')
    return expected


def model_cases() -> list[dict]:
    cases = []
    configurations = [
        ('all_fields_missing', {target: {field: True for field in FIELDS} for target in TARGETS}),
        ('descriptions_only', {target: {field: field == 'description' for field in FIELDS} for target in TARGETS}),
        ('owner_and_lineage', {'stg': {'description': False, 'owner': True, 'lineage': False},
                               'mart': {'description': False, 'owner': False, 'lineage': True}}),
        ('already_curated', {target: {field: False for field in FIELDS} for target in TARGETS}),
    ]
    for case_id, missing in configurations:
        evidence = build_evidence(missing)
        cases.append({'case_id': case_id, 'evidence': evidence, 'expected': expected_plan(evidence)})
    for case_id, update in [('unverified_evidence', {'verified': False}),
                            ('unrelated_scope', {'scope': 'out-of-scope'})]:
        evidence = {**copy.deepcopy(cases[0]['evidence']), **update}
        cases.append({'case_id': case_id, 'evidence': evidence, 'expected': expected_plan(evidence)})
    return cases
