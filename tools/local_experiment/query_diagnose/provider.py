"""One local-only advisory inference over typed, unnamed observation facts."""
import json

from tools.local_experiment.llm.providers import LocalProvider, ProviderError
from tools.local_experiment.query_diagnose import contract as C


class DiagnoseProvider(LocalProvider):
    def request_body(self, phase, tool_evidence=None):
        if phase != 'query_diagnose':
            raise ProviderError('unknown bounded diagnosis phase')
        evidence = C.validate_evidence(tool_evidence)
        instruction = (
            'Review the supplied bounded observations of one previously executed SELECT. '
            'Return only one JSON object with exactly schema_version, finding_ids, next_step_ids, '
            'root_cause, performance_gain. schema_version is integer 1. '
            'finding_ids is every findings.id, sorted without duplicates. next_step_ids is every '
            'findings.next_step_id plus MEASURE_BEFORE_OPTIMIZING, sorted without duplicates. '
            'root_cause must be undetermined and performance_gain must be unmeasured. '
            'Zero is known, null is unknown. Missing retained history has no proven cause. '
            'Positive queued time is an observation, not proof of a bottleneck. '
            'EXPLAIN and table statistics are estimates, not measured resource consumption. '
            'The original SELECT was not executed again. Next steps are review prompts only. '
            'Never add SQL, tools, parameters, commands, free text, recommendations, names, or other fields.'
        )
        body = {'model':self.profile['model'], 'temperature':0,
                'max_tokens':self.profile['max_output_tokens'], 'stream':False,
                'messages':[{'role':'system','content':instruction},
                            {'role':'user','content':json.dumps({'phase':phase,'evidence':evidence},
                                                              separators=(',', ':'), ensure_ascii=True)}]}
        if 'enable_thinking' in self.profile:
            body['enable_thinking'] = self.profile['enable_thinking']
        if len(json.dumps(body).encode()) > 8192:
            raise ProviderError('diagnosis request exceeds the bounded input limit')
        return body
