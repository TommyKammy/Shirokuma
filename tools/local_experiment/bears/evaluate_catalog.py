#!/usr/bin/env python3
"""Evaluate bounded Catalog Curator missing-field selection and refusal."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import uuid

from catalog_provider import CatalogProvider
from catalog_contract import model_cases, validate_plan
from providers import ProviderError, load_config, validate_config
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
REPEATS = 2


def evaluate(config: dict, output: Path, *, model=None, client=None, audit_module=None) -> dict:
    validate_config(config)
    if config['active_provider'] != 'local':
        raise ValueError('Catalog evaluation requires an explicitly configured local model')
    profile = config['providers']['local']
    disable_external_tracing()
    model = model if model is not None else CatalogProvider(profile)
    agenthouse = audit_module if audit_module is not None else agenthouse_module()
    client = client if client is not None else agenthouse.Client()
    output.mkdir(parents=True, mode=0o700)
    task_id, trace_id = 'catalog-eval-' + uuid.uuid4().hex, uuid.uuid4().hex
    cases = model_cases()
    report = {'result': 'failed', 'task_id': task_id, 'trace_id': trace_id,
              'trace_kind': 'evaluation correlation identifier; no exported spans',
              'scope': 'synthetic missing-field selection and refusal; no metadata reads or writes; not general catalog quality evaluation',
              'provider': profile['provider'], 'model': profile['model'], 'retries': 0,
              'requested_inferences': len(cases) * REPEATS, 'attempts': [], 'evidence_path': str(output)}
    started = time.monotonic()
    try:
        if client.query('SELECT 1').strip() != '1':
            raise ValueError('audit preflight failed')
        for case in cases:
            for repeat in range(1, REPEATS + 1):
                attempt = {'case_id': case['case_id'], 'repeat': repeat, 'result': 'failed',
                           'expected': case['expected'], 'audit': 'pending'}
                call_started = time.monotonic()
                try:
                    response = model.complete('catalog_plan', case['evidence'])
                    attempt['metrics'] = safe_metrics(response, int((time.monotonic() - call_started) * 1000))
                except Exception as error:
                    attempt['metrics'] = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                                      int((time.monotonic() - call_started) * 1000))
                    attempt['failure_code'] = 'model_response_failed'
                else:
                    try:
                        plan = validate_plan(response['content'], case['evidence'])
                        if plan != case['expected']:
                            raise ValueError('catalog plan differs from golden fixture')
                        attempt.update(result='passed', plan=plan)
                    except Exception:
                        attempt['failure_code'] = 'catalog_plan_rejected'
                report['attempts'].append(attempt)
                event = agenthouse.event('llm.catalog_eval', 'llm-local-compatible', agent_id='catalog-bear-local-evaluation',
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
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/catalog-evaluation' / uuid.uuid4().hex
    try:
        report = evaluate(load_config(args.config), output)
    except Exception:
        report = {'result': 'failed', 'failure_code': 'configuration_or_setup_failed', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'attempts'}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
