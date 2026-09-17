"""Local advisory policy classification; no permission or manifest generation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'llm'))
from providers import LocalProvider, ProviderError
from policy_contract import validate_evidence


class PolicyProvider(LocalProvider):
    def request_body(self, phase: str, tool_evidence: dict | None = None) -> dict:
        if phase != 'policy_classify':
            raise ProviderError('unknown advisory policy inference phase')
        evidence = validate_evidence(tool_evidence)
        instruction = (
            'Classify only the supplied fixed candidates using the supplied rules. This is advisory, never authorization. '
            'Return only one JSON object with exactly schema_version, classifications, verification_ids. '
            'schema_version must be integer 1. Copy verification_ids exactly from input. '
            'classifications must contain one object per candidate with exactly id, violation_ids, limit_ids. '
            'For native-baseline, put all applicable P rule IDs in violation_ids. Rules apply only to protected namespace_scope. '
            'For outside namespace_scope, violation_ids is [] and limit_ids is ["OUTSIDE_BASELINE_NAMESPACE"]. '
            'For protected candidates, limit_ids is []. For compliant protected candidates, violation_ids is []. '
            'For authorization-boundary, violation_ids is always []; put all applicable B rule IDs in limit_ids. '
            'Local commits and recreation of newly generated disposable experiment data have no matching B limits. '
            'An empty list only reports no matching supplied rule; it does not permit or approve an action. '
            'Never return allowed, approved, Tier, permission, commands, manifests, SQL, explanations or additional fields.'
        )
        body = {'model': self.profile['model'], 'temperature': 0, 'max_tokens': self.profile['max_output_tokens'],
                'stream': False, 'messages': [{'role': 'system', 'content': instruction},
                    {'role': 'user', 'content': json.dumps({'phase': phase, 'evidence': evidence})}]}
        if 'enable_thinking' in self.profile:
            body['enable_thinking'] = self.profile['enable_thinking']
        if len(json.dumps(body).encode()) > 8192:
            raise ProviderError('advisory policy request exceeds the bounded input limit')
        return body
