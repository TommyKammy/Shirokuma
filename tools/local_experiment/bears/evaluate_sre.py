#!/usr/bin/env python3
"""Evaluate local SRE read selection against synthetic healthy and adverse snapshots."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time
import uuid

from sre_provider import SREProvider
from sre_contract import COMPONENTS, build_evidence, validate_triage
from sre_evaluation_cases import (SIGNAL_SUITE, SIGNAL_DATASET_SHA256, signal_cases,
                                  dataset_sha256, validate_signal_triage)
from providers import ProviderError, load_config, validate_config
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
REPEATS = 2


def cases(suite: str = 'baseline') -> list[dict]:
    if suite == SIGNAL_SUITE:
        return signal_cases()
    if suite != 'baseline':
        raise ValueError('unknown fixed SRE evaluation suite')
    stamp = '2026-09-17T00:00:00.000000Z'
    doctor = {'profile': 'local-experiment', 'context': 'colima-mac-studio-solo', 'observed_at': stamp,
              'checks': [{'name': 'cluster', 'status': 'healthy', 'summary': 'fixture'},
                         {'name': 'flux', 'status': 'informational', 'summary':
                          'root Kustomization/flux-system/flux-system is intentionally suspended for local-experiment'}]}
    pods = {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False,
            'pods': [{'name': 'sample-pod', 'uid': 'pod-1', 'phase': 'Running',
                      'ready_containers': 1, 'total_containers': 1, 'restarts': 0}]}
    events = {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False, 'events': []}
    platform = {'profile': 'local-experiment', 'observed_at': stamp, 'ready': True,
                'components': {name: {'ready': True, 'status': 'ready'} for name in COMPONENTS}}
    result = []
    for name in ('intentional_flux_only', 'historical_restart', 'critical_pod',
                 'warning_event', 'doctor_degraded', 'event_list_truncated',
                 'runtime_degraded', 'runtime_unknown'):
        d, p, e, runtime = copy.deepcopy((doctor, pods, events, platform))
        if name == 'historical_restart':
            p['pods'][0]['restarts'] = 4
        elif name == 'critical_pod':
            p['pods'][0].update(phase='Pending', ready_containers=0)
            p['pods'].append({'name': 'second-unready', 'uid': 'pod-2', 'phase': 'Running',
                              'ready_containers': 0, 'total_containers': 1, 'restarts': 2})
        elif name == 'warning_event':
            e['events'] = [{'uid': 'event-1', 'object_uid': 'node-1', 'type': 'Warning',
                            'object_kind': 'Node', 'object_name': 'fixture-node',
                            'reason': 'MemoryPressure', 'count': 1, 'last_observed_at': stamp}]
        elif name == 'doctor_degraded':
            d['checks'][0]['status'] = 'degraded'
        elif name == 'event_list_truncated':
            e['truncated'] = True
        elif name == 'runtime_degraded':
            runtime['ready'] = False
            runtime['components']['superset'] = {'ready': False, 'status': 'not-ready'}
            runtime['components']['metadata'] = {'ready': False, 'status': 'not-ready'}
            # Both critical source classes must fit within the two-read budget.
            p['pods'][0].update(phase='Failed', ready_containers=0)
        elif name == 'runtime_unknown':
            runtime['ready'] = False
            runtime['components']['loki'] = {'ready': False, 'status': 'unknown'}
        result.append({'case_id': name, 'evidence': build_evidence(d, p, e, runtime)})
    for name in ('container_exit_nonzero', 'container_oom_reported', 'container_image_pull_wait',
                 'container_backoff_wait', 'container_previous_nonzero', 'container_previous_oom',
                 'container_details_unknown', 'container_details_truncated'):
        d, p, e, runtime = copy.deepcopy((doctor, pods, events, platform))
        state = dict(kind='running', reason=None, exit_code=None, started_at=stamp, finished_at=None)
        row = dict(name='application', ready=True, restarts=0, state=state, last_termination=None)
        observed = dict(status='complete', scope='application-containers', containers=[row])
        if name in ('container_exit_nonzero', 'container_oom_reported', 'container_previous_nonzero', 'container_previous_oom'):
            historical = name.startswith('container_previous')
            terminated = dict(kind='terminated', reason='OOMKilled' if name.endswith(('oom', 'reported')) else 'Error',
                              exit_code=137, started_at=stamp, finished_at=stamp)
            if historical:
                row.update(last_termination=terminated, restarts=1)
                p['pods'][0]['restarts'] = 1
            else:
                row.update(state=terminated, ready=False)
                p['pods'][0].update(phase='Failed', ready_containers=0)
        elif name in ('container_image_pull_wait', 'container_backoff_wait'):
            row.update(ready=False, state=dict(kind='waiting', exit_code=None, started_at=None, finished_at=None,
                reason='ImagePullBackOff' if name == 'container_image_pull_wait' else 'CrashLoopBackOff'))
            p['pods'][0].update(ready_containers=0)
        elif name == 'container_details_unknown':
            observed.update(status='unknown', containers=[])
        else:
            observed.update(status='truncated', containers=[dict(copy.deepcopy(row), name=f'application-{i}') for i in range(8)])
            p['pods'][0].update(total_containers=9, ready_containers=9)
        p['pods'][0]['container_observation'] = observed
        result.append({'case_id': name, 'evidence': build_evidence(d, p, e, runtime)})
    return result


def evaluate(config: dict, output: Path, *, model=None, client=None, audit_module=None,
             suite: str = 'baseline') -> dict:
    fixed_cases = cases(suite)
    validate_config(config)
    if config['active_provider'] != 'local':
        raise ValueError('SRE evaluation requires the explicitly configured local model')
    profile = config['providers']['local']
    signals = suite == SIGNAL_SUITE
    if signals:
        if dataset_sha256(fixed_cases) != SIGNAL_DATASET_SHA256:
            raise ValueError('fixed SRE signal dataset changed')
        # Validate every actual request body offline before any client or output.
        body_provider = SREProvider(profile)
        for case in fixed_cases:
            body_provider.request_body('sre_triage', case['evidence'])
    disable_external_tracing()
    model = model if model is not None else SREProvider(profile)
    agenthouse = audit_module if audit_module is not None else agenthouse_module()
    client = client if client is not None else agenthouse.Client()
    output.mkdir(parents=True, mode=0o700)
    task_id, trace_id = 'sre-eval-' + uuid.uuid4().hex, uuid.uuid4().hex
    report = {'result': 'failed', 'task_id': task_id, 'trace_id': trace_id,
              'trace_kind': 'evaluation correlation identifier; no exported spans',
              'scope': 'synthetic read-selection contract; no MCP calls, fault injection or remediation',
              'provider': profile['provider'], 'model': profile['model'], 'retries': 0,
              'requested_inferences': len(fixed_cases) * REPEATS, 'attempts': [], 'evidence_path': str(output)}
    if signals:
        report.update(suite=SIGNAL_SUITE, dataset_sha256=SIGNAL_DATASET_SHA256)
        private_write(output / 'result.json', report)
    started = time.monotonic()
    try:
        if client.query('SELECT 1').strip() != '1':
            raise ValueError('audit preflight failed')
        for case in fixed_cases:
            for repeat in range(1, REPEATS + 1):
                attempt = {'case_id': case['case_id'], 'repeat': repeat, 'result': 'failed',
                           'expected_health': case['evidence']['health'], 'audit': 'pending'}
                call_started = time.monotonic()
                try:
                    response = model.complete('sre_triage', case['evidence'])
                    attempt['metrics'] = safe_metrics(response, int((time.monotonic() - call_started) * 1000))
                except Exception as error:
                    attempt['metrics'] = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                                      int((time.monotonic() - call_started) * 1000))
                    attempt['failure_code'] = 'model_response_failed'
                else:
                    try:
                        proposal = (validate_signal_triage(case, response['content']) if signals
                                    else validate_triage(response['content'], case['evidence']))
                        attempt.update(result='passed', selected_tools=[x['tool'] for x in proposal['checks']])
                        if signals:
                            attempt['triage'] = proposal
                    except Exception:
                        attempt['failure_code'] = 'triage_contract_rejected'
                report['attempts'].append(attempt)
                value = agenthouse.event('llm.sre_eval', 'llm-local-compatible', agent_id='sre-local-evaluation',
                    task_id=task_id, trace_id=trace_id, status='success' if attempt['result'] == 'passed' else 'failed',
                    model_provider=profile['provider'], model_name=profile['model'], **attempt['metrics'])
                attempt['event_id'] = value['event_id']
                private_write(output / 'result.json', report)
                try:
                    agenthouse.insert(client, value)
                    attempt['audited_event'] = verify_audit(client, value)
                    attempt['audit'] = 'verified'
                except Exception:
                    attempt['audit'] = 'failed'
                    raise ValueError('audit delivery or readback failed') from None
                finally:
                    original = sys.exc_info()[1]
                    try:
                        private_write(output / 'attempts.jsonl', attempt, append=True)
                    except BaseException:
                        if signals and original is not None and not isinstance(original, Exception):
                            raise original
                        raise
        if all(x['result'] == 'passed' for x in report['attempts']):
            report['result'] = 'passed'
    except Exception:
        report['failure_code'] = 'evaluation_audit_or_storage_failed'
    except BaseException:
        # Preserve the first cancellation even if saving the partial run fails.
        # There is no automatic resume/retry; an unfinished call stays unknown.
        if signals:
            report['failure_code'] = 'evaluation_interrupted'
            try:
                _finish_report(report, started, output)
            except BaseException:
                pass
        raise
    _finish_report(report, started, output)
    return report


def _finish_report(report, started, output):
    attempts = report['attempts']
    report.update(attempted_inferences=len(attempts), passed_inferences=sum(x['result'] == 'passed' for x in attempts),
                  audited_inferences=sum(x['audit'] == 'verified' for x in attempts),
                  elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
    report['usage'] = {'inference_cost_estimate': None, 'duration_ms': sum(x['metrics']['duration_ms'] for x in attempts)}
    for key in ('prompt_tokens', 'completion_tokens'):
        values = [x['metrics'][key] for x in attempts]
        report['usage'][key] = sum(values) if values and all(x is not None for x in values) else None
    private_write(output / 'result.json', report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    parser.add_argument('--suite', choices=('baseline', SIGNAL_SUITE), default='baseline')
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/sre-evaluation' / uuid.uuid4().hex
    try:
        report = evaluate(load_config(args.config), output, suite=args.suite)
    except Exception:
        report = {'result': 'failed', 'failure_code': 'configuration_or_setup_failed', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'attempts'}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
