"""Local Catalog Curator inference returns only missing-field and evidence IDs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'llm'))
from providers import LocalProvider, ProviderError
from catalog_contract import validate_evidence


class CatalogProvider(LocalProvider):
    def request_body(self, phase: str, tool_evidence: dict | None = None) -> dict:
        if phase != 'catalog_plan':
            raise ProviderError('unknown Catalog Curator inference phase')
        evidence = validate_evidence(tool_evidence)
        instruction = (
            'Select only missing metadata fields for two historical generated catalog entities. '
            'Return one JSON object, no Markdown or commentary, with exactly schema_version, decision, actions, evidence_ids. '
            'schema_version must be integer 1. Copy all six evidence_ids exactly once. '
            'If verified is false or scope is not generated-history, decision must be unsupported and actions must be []. '
            'Otherwise produce one action for every field in each target missing_fields list: '
            'each action has exactly target and field; target is stg or mart, field is description, owner, or lineage. '
            'Include every missing field exactly once, and include no field that is already present. '
            'decision is curate when actions are nonempty, otherwise noop. '
            'Do not invent table identities, SQL, paths, owners, descriptions, reasons, commands or additional fields. '
            'The host alone supplies content and existing owner identity from the verified historical run. '
            'Historical entities do not imply that generated SQL tables still exist.'
        )
        body = {'model': self.profile['model'], 'temperature': 0,
                'max_tokens': self.profile['max_output_tokens'], 'stream': False,
                'messages': [{'role': 'system', 'content': instruction},
                             {'role': 'user', 'content': json.dumps({'phase': phase, 'evidence': evidence})}]}
        if 'enable_thinking' in self.profile:
            body['enable_thinking'] = self.profile['enable_thinking']
        if len(json.dumps(body).encode()) > 8192:
            raise ProviderError('catalog request exceeds the bounded input limit')
        return body
