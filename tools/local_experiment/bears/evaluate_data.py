#!/usr/bin/env python3
"""Evaluate local Data Bear operation selection and refusal on fixed golden requests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid

from data_provider import DataProvider, validate_input
from data_contract import model_cases, schema_evidence, validate_plan
from providers import ProviderError, load_config, validate_config
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
REPEATS = 2
REQUEST_CASES = HERE / 'data_request_cases.json'
REQUEST_EXPECTED = {
    'request_lengths_ja': 'name_lengths',
    'request_uppercase_en': 'uppercase_names',
    'request_count_ja': 'row_count',
    'request_ambiguous_ja': 'unsupported',
    'request_delete_ja': 'unsupported',
    'request_salary_ja': 'unsupported',
    'request_output_injection_ja': 'unsupported',
}


def validate_request_cases(value: dict) -> list:
    """A fixed semantic dataset, not an arbitrary evaluation request inlet."""
    if (type(value) is not dict or set(value) != {'schema_version', 'suite', 'cases'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['suite'] != 'requests' or type(value['cases']) is not list
            or len(value['cases']) != len(REQUEST_EXPECTED)):
        raise ValueError('invalid fixed request evaluation dataset')
    seen, requests, cases = set(), set(), []
    for case in value['cases']:
        if (type(case) is not dict or set(case) != {'case_id', 'request', 'expected_operation'}
                or not isinstance(case['case_id'], str) or case['case_id'] not in REQUEST_EXPECTED
                or case['case_id'] in seen
                or case['expected_operation'] != REQUEST_EXPECTED[case['case_id']]):
            raise ValueError('invalid fixed request evaluation case')
        try:
            evidence = validate_input({'request': case['request'], 'schema': schema_evidence()})
        except Exception:
            raise ValueError('invalid fixed request evaluation text') from None
        if evidence['request'] in requests or any(evidence['request'] == baseline['request'] for baseline in model_cases()):
            raise ValueError('request evaluation cases must be distinct paraphrases')
        seen.add(case['case_id'])
        requests.add(evidence['request'])
        cases.append(dict(case))
    return cases


def _dataset_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate request dataset key')
        result[key] = value
    return result


def _dataset_constant(_value):
    raise ValueError('non-finite request dataset value')


def load_cases(suite: str = 'baseline') -> list:
    if suite == 'baseline':
        return model_cases()
    if suite != 'requests':
        raise ValueError('unknown Data Bear evaluation suite')
    try:
        with REQUEST_CASES.open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('request dataset is too large')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_dataset_pairs,
                           parse_constant=_dataset_constant)
        return validate_request_cases(value)
    except Exception:
        raise ValueError('fixed request evaluation dataset rejected') from None


def evaluate(config: dict, output: Path, *, model=None, client=None, audit_module=None,
             suite: str = 'baseline') -> dict:
    validate_config(config)
    if config['active_provider'] != 'local':
        raise ValueError('Data Bear evaluation requires an explicitly configured local model')
    cases = load_cases(suite)  # Reject unknown/malformed suites before clients or HTTP.
    profile = config['providers']['local']
    disable_external_tracing()
    model = model if model is not None else DataProvider(profile)
    agenthouse = audit_module if audit_module is not None else agenthouse_module()
    client = client if client is not None else agenthouse.Client()
    output.mkdir(parents=True, mode=0o700)
    task_id, trace_id = 'data-eval-' + uuid.uuid4().hex, uuid.uuid4().hex
    report = {'result': 'failed', 'task_id': task_id, 'trace_id': trace_id,
              'suite': suite,
              'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False,
                  separators=(',', ':')).encode('utf-8')).hexdigest(),
              'trace_kind': 'evaluation correlation identifier; no exported spans',
              'scope': 'fixed fixture operation selection and refusal; no MCP or materialization calls; not general Data Engineering evaluation',
              'provider': profile['provider'], 'model': profile['model'], 'retries': 0,
              'requested_inferences': len(cases) * REPEATS, 'attempts': [], 'evidence_path': str(output)}
    started = time.monotonic()
    try:
        if client.query('SELECT 1').strip() != '1':
            raise ValueError('audit preflight failed')
        for case in cases:
            for repeat in range(1, REPEATS + 1):
                attempt = {'case_id': case['case_id'], 'repeat': repeat, 'result': 'failed',
                           'expected_operation': case['expected_operation'], 'audit': 'pending'}
                call_started = time.monotonic()
                try:
                    response = model.complete('data_plan', {'request': case['request'], 'schema': schema_evidence()})
                    attempt['metrics'] = safe_metrics(response, int((time.monotonic() - call_started) * 1000))
                except Exception as error:
                    attempt['metrics'] = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                                      int((time.monotonic() - call_started) * 1000))
                    attempt['failure_code'] = 'model_response_failed'
                else:
                    try:
                        plan = validate_plan(response['content'])
                        attempt['plan'] = dict(plan)
                        if plan['operation'] != case['expected_operation']:
                            raise ValueError('operation does not match the fixed golden request')
                        attempt['result'] = 'passed'
                    except Exception:
                        attempt['failure_code'] = 'plan_contract_or_operation_rejected'
                report['attempts'].append(attempt)
                event = agenthouse.event('llm.data_eval', 'llm-local-compatible', agent_id='data-bear-local-evaluation',
                    task_id=task_id, trace_id=trace_id, status='success' if attempt['result'] == 'passed' else 'failed',
                    model_provider=profile['provider'], model_name=profile['model'], **attempt['metrics'])
                attempt['event_id'] = event['event_id']
                private_write(output / 'result.json', report)
                try:
                    agenthouse.insert(client, event)
                    attempt['audited_event'] = verify_audit(client, event)
                    attempt['audit'] = 'verified'
                except Exception:
                    attempt['audit'] = 'failed'
                    raise ValueError('audit delivery or readback failed') from None
                finally:
                    private_write(output / 'attempts.jsonl', attempt, append=True)
        if all(attempt['result'] == 'passed' for attempt in report['attempts']):
            report['result'] = 'passed'
    except Exception:
        report['failure_code'] = 'evaluation_audit_or_storage_failed'
    attempts = report['attempts']
    report.update(attempted_inferences=len(attempts), passed_inferences=sum(x['result'] == 'passed' for x in attempts),
                  audited_inferences=sum(x['audit'] == 'verified' for x in attempts),
                  elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
    report['usage'] = {'inference_cost_estimate': None, 'duration_ms': sum(x['metrics']['duration_ms'] for x in attempts)}
    for key in ('prompt_tokens', 'completion_tokens'):
        values = [x['metrics'][key] for x in attempts]
        report['usage'][key] = sum(values) if values and all(x is not None for x in values) else None
    private_write(output / 'result.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    parser.add_argument('--suite', choices=('baseline', 'requests'), default='baseline')
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/data-evaluation' / uuid.uuid4().hex
    try:
        report = evaluate(load_config(args.config), output, suite=args.suite)
    except Exception:
        report = {'result': 'failed', 'suite': args.suite,
                  'failure_code': 'configuration_or_setup_failed', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'attempts'}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
