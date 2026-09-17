"""Local Query Doctor inference can select only two fixed additional reads."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'llm'))
from providers import LocalProvider, ProviderError
from query_contract import validate_evidence


class QueryProvider(LocalProvider):
    def request_body(self, phase: str, tool_evidence: dict | None = None) -> dict:
        if phase != 'query_plan':
            raise ProviderError('unknown Query Doctor inference phase')
        evidence = validate_evidence(tool_evidence)
        instruction = (
            'Select bounded follow-up observations for two fixed SELECT queries over a two-row local fixture. '
            'Return only one JSON object with exactly schema_version, checks, root_cause, performance_gain. '
            'schema_version must be integer 1, root_cause must be undetermined, performance_gain must be unmeasured. '
            'checks contains at most two objects, each with exactly tool and finding_ids. '
            'The only tools are fixture_stats and own_query_history. Do not repeat a tool. '
            'For each distinct tool in findings, include one check listing every finding id that names that tool. '
            'If findings is empty, checks must be []. Do not request reads unrelated to a finding. '
            'Zero is a known numeric value, not missing. Plan estimates are not measured execution metrics. '
            'Missing estimates, incomplete history, failed states or missing durations do not establish a root cause. '
            'This fixture cannot establish general performance or improvement. '
            'Never return SQL, commands, arguments, rewrites, entity names, explanations or additional fields.'
        )
        body = {'model': self.profile['model'], 'temperature': 0,
                'max_tokens': self.profile['max_output_tokens'], 'stream': False,
                'messages': [{'role': 'system', 'content': instruction},
                             {'role': 'user', 'content': json.dumps({'phase': phase, 'evidence': evidence})}]}
        if 'enable_thinking' in self.profile:
            body['enable_thinking'] = self.profile['enable_thinking']
        if len(json.dumps(body).encode()) > 8192:
            raise ProviderError('Query Doctor request exceeds the bounded input limit')
        return body
