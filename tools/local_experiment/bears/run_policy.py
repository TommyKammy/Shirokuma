#!/usr/bin/env python3
"""Classify fixed local policy candidates and verify them using server dry-runs only."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import TypedDict
import uuid

from policy_provider import PolicyProvider
from policy_contract import batches, expected_plan, validate_observation, validate_plan, verify_receipt
from providers import ProviderError, load_config, unknown_metrics, validate_config
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing
from run_sre import module, SpanFile
from run_sre_llm import AUDIT_FIELDS, verify_audits
from run_catalog import trace_lifecycle, verify_saved_spans

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(LOCAL))
import local_session as SESSION
from policy.gatekeeper import GatekeeperObserver


class PolicyBearError(RuntimeError):
    pass


class State(TypedDict, total=False):
    observation: dict
    truth: dict
    plans: list
    inferences: list
    verification: dict
    assessment: dict


def build_graph(model, observe, verify, audit, tracer, *, record_plan=None):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    from opentelemetry.trace import Status, StatusCode

    async def collect(state):
        action = 'bear.policy.observe'
        with tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                value = await observe()
                if type(value) is not dict or set(value) != {'observation', 'truth'}:
                    raise PolicyBearError('policy observation must separate inputs from host expectations')
                observed = validate_observation(value['observation'])
                for evidence in batches(observed):
                    expected_plan(evidence, value['truth'])
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR, 'OBSERVATION_FAILED'))
                audit(action, 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                if not isinstance(error, Exception):
                    raise
                raise PolicyBearError('fixed policy observation failed') from None
            audit(action, 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
            span.set_status(Status(StatusCode.OK))
            return {'observation': observed, 'truth': value['truth']}

    async def classify(state):
        plans, inferences = [], []
        for index, evidence in enumerate(batches(state['observation']), 1):
            action = 'llm.policy_classify'
            with tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False) as span:
                started = time.monotonic()
                try:
                    response = await asyncio.to_thread(model.complete, 'policy_classify', evidence)
                except BaseException as error:
                    metrics = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                           int((time.monotonic() - started) * 1000))
                    span.set_status(Status(StatusCode.ERROR, 'MODEL_RESPONSE_FAILED'))
                    audit(action, 'failed', metrics, span, inference=True)
                    if not isinstance(error, Exception):
                        raise
                    raise PolicyBearError('policy model response failed') from None
                metrics = safe_metrics(response, int((time.monotonic() - started) * 1000))
                try:
                    plan = validate_plan(response['content'], evidence, state['truth'])
                except Exception:
                    span.set_status(Status(StatusCode.ERROR, 'MODEL_CLASSIFICATION_REJECTED'))
                    audit(action, 'blocked', metrics, span, inference=True)
                    raise PolicyBearError('model policy classification differs from independent host expectations') from None
                if record_plan is not None:
                    try:
                        record_plan({'batch': index, 'plan': plan, 'metrics': metrics})
                    except BaseException as error:
                        span.set_status(Status(StatusCode.ERROR, 'PLAN_EVIDENCE_STORAGE_FAILED'))
                        audit(action, 'failed', metrics, span, inference=True)
                        if not isinstance(error, Exception):
                            raise
                        raise PolicyBearError('policy plan evidence could not be stored') from None
                audit(action, 'success', metrics, span, inference=True)
                span.set_status(Status(StatusCode.OK))
                plans.append(plan)
                inferences.append(metrics)
        return {'plans': plans, 'inferences': inferences}

    async def dry_run(state):
        action = 'bear.policy.verify'
        with tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                # No plan, manifest, scope, name, permission or candidate subset is
                # passed to the host verifier. It always checks its full fixed corpus.
                receipt = verify_receipt(await verify(), state['observation'], state['truth'])
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR, 'DRY_RUN_VERIFICATION_FAILED'))
                audit(action, 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                if not isinstance(error, Exception):
                    raise
                raise PolicyBearError('fixed server dry-run verification failed') from None
            audit(action, 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
            span.set_status(Status(StatusCode.OK))
            return {'verification': receipt}

    async def assess(state):
        receipt = verify_receipt(state['verification'], state['observation'], state['truth'])
        return {'assessment': {'authority': 'advisory_only', 'authority_granted': False,
            'server_dry_run_cases': receipt['passed_cases'], 'classified_native_candidates': 15,
            'classified_boundary_candidates': 10, 'persisted_workloads': 0, 'boundary_actions_executed': 0,
            'limits': list(state['observation']['limits'])}}

    graph = StateGraph(State)
    nodes = [('observe', collect), ('classify', classify), ('verify', dry_run), ('assess', assess)]
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_edge(START, nodes[0][0])
    for left, right in zip(nodes, nodes[1:]):
        graph.add_edge(left[0], right[0])
    graph.add_edge(nodes[-1][0], END)
    return graph.compile(checkpointer=InMemorySaver())


async def run(config: dict, output: Path) -> dict:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    validate_config(config)
    if config['active_provider'] != 'local':
        raise PolicyBearError('Policy Gatekeeper requires an explicitly configured local model')
    profile = config['providers']['local']
    model = PolicyProvider(profile)
    disable_external_tracing()
    output.mkdir(parents=True, mode=0o700)
    task_id = 'workflow-' + uuid.uuid4().hex
    agenthouse, loki = agenthouse_module(), module('policy_bear_loki', LOCAL / 'loki/manage.py')
    audit_client = agenthouse.Client()
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-policy-local-llm'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.policy', '0.1.0')
    records = []
    result = {'result': 'failed', 'scope': 'advisory-policy-classification-and-fixed-server-dry-runs', 'task_id': task_id,
              'evidence_path': str(output), 'provider': profile['provider'], 'model': profile['model'],
              'audit_records': records, 'retries': 0, 'cloud_calls': 0, 'authority_granted': False,
              'persisted_workloads': 0, 'boundary_actions_executed': 0, 'classified_batches': []}
    started = time.monotonic()
    with trace_lifecycle(provider), tracer.start_as_current_span(
            'bear.policy.assess', record_exception=False, set_status_on_exception=False) as root_span:
        trace_id = f'{root_span.get_span_context().trace_id:032x}'
        result['trace_id'] = trace_id
        root_span.set_attribute('task_id', task_id)

        def audit(action, status, metrics, span, *, inference=False):
            event = agenthouse.event(action, 'llm-local-compatible' if inference else 'native-policy-server-dry-run',
                agent_id='policy-bear-local-llm', task_id=task_id, trace_id=trace_id, status=status,
                blocked_by_policy=status == 'blocked', model_provider=profile['provider'] if inference else 'none',
                model_name=profile['model'] if inference else '', **metrics)
            record = {key: event[key] for key in AUDIT_FIELDS}
            record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
            records.append(record)
            private_write(output / 'result.json', result)
            try:
                agenthouse.insert(audit_client, event)
                verify_audit(audit_client, event)
                loki.push_record({'task_id': task_id, 'trace_id': trace_id, 'span_id': record['span_id'],
                                  'action': action, 'status': status})
            except Exception:
                record['audit_delivery'] = 'failed'
                raise PolicyBearError('policy audit delivery or readback failed') from None
            record['audit_delivery'] = 'delivered'

        def save_plan(value):
            result['classified_batches'].append(value)
            private_write(output / 'plans.jsonl', value, append=True)
            private_write(output / 'result.json', result)

        try:
            if audit_client.query('SELECT 1').strip() != '1':
                raise PolicyBearError('policy audit preflight failed')
            observer = GatekeeperObserver()

            def save_receipt(value=None):
                receipt = observer.receipt() if value is None else value
                private_write(output / 'verification-progress.json', receipt)
                return receipt

            async def observe():
                try:
                    value = observer.observe()
                    validate_observation(value)
                    private_write(output / 'observation.json', value)
                    truth = observer.host_truth()
                    private_write(output / 'host-expectations.json', truth)
                    return {'observation': value, 'truth': truth}
                finally:
                    save_receipt()

            async def verify():
                # All subprocess requests are synchronous and server-dry-run only;
                # the host verifier persists progress around every bounded request.
                try:
                    receipt = observer.verify(progress=save_receipt)
                    private_write(output / 'verification.json', receipt)
                    return receipt
                finally:
                    save_receipt()

            try:
                graph = build_graph(model, observe, verify, audit, tracer, record_plan=save_plan)
                options = {'configurable': {'thread_id': task_id}, 'recursion_limit': 10, 'callbacks': []}
                state = await graph.ainvoke({}, options)
                checkpoint = graph.get_state(options)
                if checkpoint.next or checkpoint.values.get('assessment') != state['assessment']:
                    raise PolicyBearError('Policy Gatekeeper completed checkpoint differs')
            finally:
                save_receipt()
            result.update(plans=state['plans'], inferences=state['inferences'], assessment=state['assessment'],
                          verification=state['verification'], checkpoint='completed-in-memory')
            audit('bear.policy.assess', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
            ids = [str(uuid.UUID(record['event_id'])) for record in records]
            quoted = ','.join("'" + value + "'" for value in ids)
            rows = json.loads(audit_client.query('SELECT ' + ', '.join(AUDIT_FIELDS) +
                ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
            verify_audits(rows, loki.query_task(task_id), [], records, task_id, trace_id)
            result.update(result='passed', correlated_events=len(rows))
            root_span.set_status(Status(StatusCode.OK))
        except BaseException as error:
            result.update(result='failed', error_code='POLICY_OBSERVATION_CLASSIFICATION_DRYRUN_OR_AUDIT_FAILED')
            result.pop('checkpoint', None)
            root_span.set_status(Status(StatusCode.ERROR, result['error_code']))
            if not any(row['action_type'] == 'bear.policy.assess' for row in records):
                try:
                    audit('bear.policy.assess', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
                except Exception:
                    pass
            private_write(output / 'result.json', result)
            if not isinstance(error, Exception):
                raise
    if result['result'] == 'passed':
        try:
            verify_saved_spans(output / 'spans.jsonl', records, trace_id)
            result['saved_otel_spans_verified'] = len(records)
        except Exception:
            result.update(result='failed', error_code='POLICY_SAVED_TRACE_VERIFICATION_FAILED')
            result.pop('checkpoint', None)
    private_write(output / 'result.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/policy-bear' / uuid.uuid4().hex
    try:
        with SESSION.interruption_handlers():
            report = asyncio.run(run(load_config(args.config), output))
    except SESSION.SessionInterrupted as error:
        report = {'result': 'interrupted', 'signal': error.signum, 'evidence_path': str(output),
                  'authority_granted': False, 'persisted_workloads': 0, 'boundary_actions_executed': 0}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'interruption.json', report)
        print(json.dumps(report))
        return 128 + error.signum
    except Exception:
        report = {'result': 'failed', 'error_code': 'POLICY_CONFIGURATION_OR_SETUP_FAILED', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
