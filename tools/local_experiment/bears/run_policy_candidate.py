#!/usr/bin/env python3
"""Advisory classification of one caller Pod, followed by one guarded server dry-run."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-experiment/policy-candidate-bear'
sys.path[:0] = [str(HERE), str(LOCAL)]

from policy.candidate import prepare
from policy.candidate_observer import CandidateObserver, digest
import policy_contract as C
from policy_provider import PolicyProvider
from providers import ProviderError, load_config, validate_config, unknown_metrics
from evaluation import safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing
from run_sre import module, SpanFile
from run_sre_llm import AUDIT_FIELDS, verify_audits, trace_lifecycle, preserve_interruption
from run_catalog import verify_saved_spans
import local_session as SESSION


class PolicyCandidateError(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def persist(path, value):
    """Atomically record an intent before any dry-run; no raw caller object is accepted here."""
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
            stream.write(json.dumps(value, sort_keys=True, ensure_ascii=True) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        preserve_interruption(temporary.unlink, missing_ok=True)


def create_output(output):
    if not re.fullmatch('[0-9a-f]{32}', output.name) or output.exists() or output.is_symlink():
        raise PolicyCandidateError('OUTPUT_NOT_FRESH')
    # Checking only after mkdir can create directories through an existing
    # symlink into an unrelated (possibly retained) tree before refusing it.
    if any(path.is_symlink() for path in [output.parent, *output.parent.parents]):
        raise PolicyCandidateError('OUTPUT_PARENT_INVALID')
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if any(path.is_symlink() for path in [output.parent, *output.parent.parents]):
        raise PolicyCandidateError('OUTPUT_PARENT_INVALID')
    output.mkdir(mode=0o700)
    fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


async def run(config, output, raw):
    """Exactly one model call and at most one dry-run; a new token is required to resubmit."""
    output = Path(output)
    prepared = prepare(raw, output.name)  # Invalid input reaches neither baseline nor model/API.
    evidence = C.build_evidence([prepared.candidate()], 'native-baseline')
    truth = prepared.host_truth()
    C.expected_plan(evidence, truth)
    validate_config(config)
    if config['active_provider'] != 'local':
        raise PolicyCandidateError('LOCAL_MODEL_REQUIRED')
    create_output(output)
    persist(output / 'candidate.json', prepared.receipt())
    profile = config['providers']['local']
    model = PolicyProvider(profile)
    observer = CandidateObserver(prepared)
    disable_external_tracing()
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    agenthouse, loki = agenthouse_module(), module('candidate_policy_loki', LOCAL / 'loki/manage.py')
    client = agenthouse.Client()
    provider = TracerProvider(resource=Resource.create({'service.name':'shirokuma-policy-candidate'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.policy.candidate', '0.1.0')
    task_id = 'workflow-' + uuid.uuid4().hex
    records = []
    result = dict(schema_version=1, run_token=output.name, task_id=task_id,
        scope='advisory-single-pod-server-dry-run', result='running', started_at=stamp(), finished_at=None,
        workflow_complete=False, authority_granted=False, persisted_workloads=0, retries=0, cloud_calls=0,
        model_attempts=0, model_matched=False, model_metrics=None, audit_records=records,
        candidate=prepared.receipt(), error_code=None, verification=observer.receipt())
    started = time.monotonic()
    def save():
        persist(output / 'result.json', result)
    def progress(receipt):
        result['verification'] = receipt
        persist(output / 'verification-progress.json', receipt)
        save()
    with trace_lifecycle(provider), tracer.start_as_current_span(
            'bear.policy_candidate.assess', record_exception=False, set_status_on_exception=False) as root_span:
        trace_id = f'{root_span.get_span_context().trace_id:032x}'
        result['trace_id'] = trace_id; root_span.set_attribute('task_id', task_id)
        def audit(action, status, metrics, span, *, inference=False):
            event = agenthouse.event(action, 'llm-local-compatible' if inference else 'native-policy-server-dry-run',
                agent_id='policy-candidate-local', task_id=task_id, trace_id=trace_id, status=status,
                blocked_by_policy=status == 'blocked', model_provider=profile['provider'] if inference else 'none',
                model_name=profile['model'] if inference else '', **metrics)
            record = {key:event[key] for key in AUDIT_FIELDS}
            record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
            records.append(record); save()
            try:
                agenthouse.insert(client, event); verify_audit(client, event)
                loki.push_record(dict(task_id=task_id,trace_id=trace_id,span_id=record['span_id'],action=action,status=status))
            except Exception:
                record['audit_delivery'] = 'failed'
                raise PolicyCandidateError('AUDIT_DELIVERY_FAILED') from None
            record['audit_delivery'] = 'delivered'
        async def phase(action, call, *, inference=False):
            with tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False) as span:
                began = time.monotonic(); metrics = None
                try:
                    value = await call()
                    metrics = result['model_metrics'] if inference else unknown_metrics(int((time.monotonic()-began)*1000))
                    audit(action, 'success', metrics, span, inference=inference)
                    span.set_status(Status(StatusCode.OK)); return value
                except BaseException as error:
                    if metrics is None:
                        metrics = (result['model_metrics'] or safe_metrics(error.metrics if isinstance(error,ProviderError) else {},
                            int((time.monotonic()-began)*1000))) if inference else unknown_metrics(int((time.monotonic()-began)*1000))
                    if inference:
                        result['model_metrics'] = metrics
                    span.set_status(Status(StatusCode.ERROR, 'POLICY_CANDIDATE_PHASE_FAILED'))
                    if not any(row['span_id'] == f'{span.get_span_context().span_id:016x}' for row in records):
                        preserve_interruption(audit, action, 'blocked' if isinstance(error,C.ContractError) else 'failed',
                                              metrics, span, inference=inference)
                    raise
        async def observe():
            observed = observer.observe()
            persist(output / 'observation.json', observed)
            progress(observer.receipt()); return observed
        async def classify():
            result['model_attempts'] = 1; save()
            began = time.monotonic()
            response = await asyncio.to_thread(model.complete, 'policy_classify', evidence)
            result['model_metrics'] = safe_metrics(response, int((time.monotonic()-began)*1000))
            plan = C.validate_plan(response['content'], evidence, truth)
            persist(output / 'classification.json', dict(plan=plan,metrics=result['model_metrics']))
            result.update(model_matched=True, plan=plan); save(); return plan
        async def verify():
            try:
                receipt = observer.verify(progress=progress)
                if receipt['result'] != 'passed' or receipt['pending'] is not None or not receipt['matched_host_expectation']:
                    raise PolicyCandidateError('DRY_RUN_NOT_VERIFIED')
                persist(output / 'verification.json', receipt); return receipt
            finally:
                preserve_interruption(progress, observer.receipt())
        try:
            save()
            if client.query('SELECT 1').strip() != '1':
                raise PolicyCandidateError('AUDIT_PREFLIGHT_FAILED')
            await phase('bear.policy_candidate.observe', observe)
            await phase('llm.policy_candidate.classify', classify, inference=True)
            result['verification'] = await phase('bear.policy_candidate.verify', verify)
            audit('bear.policy_candidate.assess', 'success', unknown_metrics(int((time.monotonic()-started)*1000)), root_span)
            quoted = ','.join("'" + str(uuid.UUID(row['event_id'])) + "'" for row in records)
            rows = json.loads(client.query('SELECT ' + ', '.join(AUDIT_FIELDS) +
                ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
            verify_audits(rows, loki.query_task(task_id), [], records, task_id, trace_id)
            result.update(result='verified', correlated_events=len(rows))
            root_span.set_status(Status(StatusCode.OK))
        except BaseException as error:
            result.update(result='failed' if isinstance(error,Exception) else 'interrupted',
                          error_code='POLICY_CANDIDATE_OBSERVATION_MODEL_DRYRUN_OR_AUDIT_FAILED')
            root_span.set_status(Status(StatusCode.ERROR, result['error_code']))
            if not any(row['action_type'] == 'bear.policy_candidate.assess' for row in records):
                try:
                    preserve_interruption(audit, 'bear.policy_candidate.assess', 'failed',
                                          unknown_metrics(int((time.monotonic()-started)*1000)), root_span)
                except Exception:
                    pass
            result['verification'] = observer.receipt()
            if not isinstance(error,Exception):
                try: save()
                finally: raise error
    if result['result'] == 'verified':
        try:
            verify_saved_spans(output / 'spans.jsonl', records, result['trace_id'])
            result.update(result='passed', workflow_complete=True, saved_otel_spans_verified=len(records))
        except Exception:
            result.update(result='failed', error_code='POLICY_CANDIDATE_SAVED_TRACE_VERIFICATION_FAILED')
    result['finished_at'] = stamp(); save()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-stdin', action='store_true', required=True)
    parser.add_argument('--run-token', default=None)
    args = parser.parse_args(argv)
    token = args.run_token or uuid.uuid4().hex
    if not re.fullmatch('[0-9a-f]{32}', token):
        print(json.dumps({'result':'not-started','error_code':'TOKEN_INVALID'})); return 1
    try:
        with SESSION.interruption_handlers():
            raw = sys.stdin.buffer.read(16385)
            prepare(raw, token)
            result = asyncio.run(run(load_config(LOCAL/'llm/providers.qwen-local.json'), STATE/token, raw))
        print(json.dumps({'result':result['result'],'run_token':token,'evidence':str((STATE/token).relative_to(ROOT))}))
        return 0 if result['result'] == 'passed' else 1
    except BaseException as error:
        print(json.dumps({'result':'unknown' if (STATE/token).exists() else 'not-started',
                          'run_token':token,'error_code':'INTERRUPTED' if not isinstance(error,Exception) else 'POLICY_CANDIDATE_FAILED'}))
        return (128 + error.signum if isinstance(error,SESSION.SessionInterrupted) else 130) if not isinstance(error,Exception) else 1


if __name__ == '__main__':
    raise SystemExit(main())
