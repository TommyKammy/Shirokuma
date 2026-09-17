#!/usr/bin/env python3
"""One Data invocation, followed by resumable curation of that verified source.

The durable host journal is a handoff checkpoint, not a durable LangGraph
checkpoint. An uncertain Data attempt is never started again under the same ID.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LOCAL))
STATE = ROOT / 'tmp/local-experiment/data-catalog'
TOKEN = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')
MAX_ATTEMPTS = 8
MAX_BYTES = 1024 * 1024
PHASES = {'data_pending', 'data_verified', 'catalog_pending', 'catalog_failed',
          'completed', 'unsupported', 'failed', 'unknown'}
OUTCOMES = {'pending', 'passed', 'unsupported', 'failed', 'interrupted', 'unknown'}
CATALOG_FILES = {'result.json', 'spans.jsonl', 'source-evidence.json', 'catalog-observation.json',
                 'plan.json', 'apply-progress.json', 'repeat-progress.json', 'apply.json',
                 'repeat.json', 'interruption.json'}
LIMITS = [
    'At most one Data invocation per handoff; an uncertain invocation is not automatically repeated.',
    'Historical catalog entities refer to verified, cleaned-up Data Plan artifacts, not live SQL tables.',
    'Completed resume validates saved evidence only; it does not observe current catalog state.',
    'Child workflows retain their own audit records and trace IDs; no shared distributed trace is claimed.',
    'Natural-language intent is model-selected; the independent oracle checks the selected typed operation.',
    'Catalog partial writes are retained. Resume uses the same source and a new attempt; no rollback is claimed.',
]


class HandoffError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise HandoffError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def token(value):
    require(type(value) is str and TOKEN.fullmatch(value), 'INVALID_HANDOFF_IDENTITY')
    return value


def modules():
    # Keep module import/CLI help stdlib-only; execution uses the existing pinned
    # LLM environment and APIs without adding another child-process lifecycle.
    import run_data
    import run_catalog
    from metadata import data_plan_evidence
    return run_data, run_catalog, data_plan_evidence


def private_directory(path):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')


def read(path):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_BYTES, 'PRIVATE_FILE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino), 'EVIDENCE_CHANGED')
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) <= MAX_BYTES and (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'EVIDENCE_CHANGED')
    return raw


def read_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'INVALID_SAVED_JSON')
            value[key] = item
        return value
    raw = read(path)
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(HandoffError('INVALID_SAVED_JSON')))
    except (ValueError, UnicodeError, RecursionError):
        raise HandoffError('INVALID_SAVED_JSON') from None
    return value, hashlib.sha256(raw).hexdigest()


def persist(directory, journal):
    """A failed durable intent write always precedes and prevents the API call."""
    private_directory(directory)
    target = directory / 'handoff.json'
    if target.exists() or target.is_symlink():
        read(target)
    temporary = directory / ('.journal-' + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(journal) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_directory(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def locked(handoff_token, *, resume):
    token(handoff_token)
    require(STATE.parent.is_dir() and STATE.parent.resolve() == STATE.parent, 'PRIVATE_PARENT_REQUIRED')
    if not STATE.exists():
        require(not resume, 'HANDOFF_NOT_FOUND')
        STATE.mkdir(mode=0o700)
        sync_directory(STATE.parent)
    private_directory(STATE)
    directory = STATE / handoff_token
    fresh = False
    if not directory.exists():
        require(not resume, 'HANDOFF_NOT_FOUND')
        try:
            directory.mkdir(mode=0o700)
            sync_directory(STATE)
            fresh = True
        except FileExistsError:
            pass
    private_directory(directory)
    fd = os.open(directory / 'handoff.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600, 'PRIVATE_LOCK_REQUIRED')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HandoffError('HANDOFF_BUSY') from None
        yield directory, fresh
    finally:
        os.close(fd)


def output_path(kind, attempt_token):
    require(kind in {'data-bear', 'catalog-bear', 'data-plans'}, 'INVALID_OUTPUT_KIND')
    return ROOT / 'tmp/local-experiment' / kind / token(attempt_token)


def reserve_output(kind):
    attempt_token = uuid.uuid4().hex
    path = output_path(kind, attempt_token)
    require(path.parent.resolve() == path.parent and not path.exists() and not path.is_symlink(), 'OUTPUT_ALREADY_EXISTS')
    return attempt_token


def validate_journal(value, handoff_token):
    require(type(value) is dict and set(value) == {'schema_version', 'handoff_token', 'created_at', 'updated_at',
        'input', 'config_sha256', 'phase', 'data', 'source', 'catalog_attempts', 'error_code', 'limits'}, 'INVALID_HANDOFF_JOURNAL')
    require(value['schema_version'] == 1 and type(value['schema_version']) is int
            and value['handoff_token'] == handoff_token and type(value['phase']) is str
            and value['phase'] in PHASES and value['limits'] == LIMITS
            and type(value['config_sha256']) is str and SHA.fullmatch(value['config_sha256']), 'INVALID_HANDOFF_JOURNAL')
    D, _, _ = modules()
    metadata = value['input']
    require(type(metadata) is dict and set(metadata) == {'case_id', 'expected_operation', 'input_mode', 'request_sha256',
        'request_characters', 'request_bytes', 'raw_request_retained', 'interpretation_verification'}, 'INVALID_HANDOFF_INPUT')
    require(metadata['raw_request_retained'] is False and type(metadata['request_sha256']) is str
            and SHA.fullmatch(metadata['request_sha256']) and type(metadata['request_characters']) is int
            and 1 <= metadata['request_characters'] <= 512 and type(metadata['request_bytes']) is int
            and 1 <= metadata['request_bytes'] <= 4096, 'INVALID_HANDOFF_INPUT')
    if metadata['case_id'] is not None:
        require(metadata == D.request_metadata(D.request_spec(case_id=metadata['case_id'])), 'INVALID_HANDOFF_INPUT')
    else:
        require(metadata['expected_operation'] is None and metadata['input_mode'] == 'natural-language'
                and metadata['interpretation_verification'] == 'model-selected-typed-plan; free-text-intent-not-independently-verified',
                'INVALID_HANDOFF_INPUT')
    data = value['data']
    require(type(data) is dict and set(data) == {'attempt_token', 'outcome', 'result_sha256'}, 'INVALID_HANDOFF_DATA')
    token(data['attempt_token'])
    attempts = value['catalog_attempts']
    require(type(attempts) is list and len(attempts) <= MAX_ATTEMPTS, 'INVALID_HANDOFF_ATTEMPTS')
    ids = [data['attempt_token']]
    for attempt in [data, *attempts]:
        require(type(attempt) is dict and type(attempt.get('outcome')) is str and attempt['outcome'] in OUTCOMES,
                'INVALID_HANDOFF_ATTEMPT')
        sha = attempt.get('result_sha256')
        require(sha is None or type(sha) is str and SHA.fullmatch(sha), 'INVALID_HANDOFF_ATTEMPT')
    for attempt in attempts:
        require(set(attempt) == {'attempt_token', 'outcome', 'result_sha256', 'files'}, 'INVALID_HANDOFF_ATTEMPT')
        ids.append(token(attempt['attempt_token']))
        files = attempt['files']
        require(files is None or type(files) is dict and set(files) <= CATALOG_FILES
                and all(type(sha) is str and SHA.fullmatch(sha) for sha in files.values()), 'INVALID_HANDOFF_ATTEMPT')
    require(len(ids) == len(set(ids)), 'DUPLICATE_HANDOFF_ATTEMPT')
    source = value['source']
    if source is not None:
        require(type(source) is dict and set(source) == {'run_token', 'operation', 'evidence_sha256', 'adapter_sha256'},
                'INVALID_HANDOFF_SOURCE')
        token(source['run_token'])
        require(source['operation'] in {'uppercase_names', 'name_lengths', 'row_count'}
                and all(type(source[key]) is str and SHA.fullmatch(source[key]) for key in ('evidence_sha256', 'adapter_sha256')),
                'INVALID_HANDOFF_SOURCE')
    require(not attempts or source is not None, 'INVALID_HANDOFF_PHASE')
    require(value['phase'] != 'completed' or source is not None and attempts and attempts[-1]['outcome'] == 'passed',
            'INVALID_HANDOFF_PHASE')
    require(source is None or data['outcome'] == 'passed', 'INVALID_HANDOFF_PHASE')
    return value


def audit_receipt(value, actions, *, tool=False, blocked=False):
    require(type(value.get('task_id')) is str and re.fullmatch(r'workflow-[0-9a-f]{32}', value['task_id'])
            and type(value.get('trace_id')) is str and re.fullmatch(r'[0-9a-f]{32}', value['trace_id']), 'INVALID_CHILD_AUDIT')
    records = value.get('audit_records')
    require(type(records) is list and len(records) == len(actions)
            and all(type(item) is dict for item in records)
            and [item.get('action_type') for item in records] == actions, 'INVALID_CHILD_AUDIT')
    ids = []
    for record in records:
        require(record.get('audit_delivery') == 'delivered' and record.get('status') == ('blocked' if blocked else 'success')
                and record.get('task_id') == value['task_id'] and record.get('trace_id') == value['trace_id'], 'INVALID_CHILD_AUDIT')
        identity = record.get('event_id')
        require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'INVALID_CHILD_AUDIT')
        ids.append(identity)
    if tool:
        tools = value.get('tool_calls')
        require(type(tools) is list and len(tools) == 1 and type(tools[0]) is dict
                and tools[0].get('tool') == 'dbt_list_models' and tools[0].get('action_type') == 'mcp.dbt_list_models',
                'INVALID_CHILD_AUDIT')
        identity = tools[0].get('event_id')
        require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'INVALID_CHILD_AUDIT')
        ids.append(identity)
    require(len(ids) == len(set(ids)) and type(value.get('correlated_events')) is int
            and value['correlated_events'] == len(ids), 'INVALID_CHILD_AUDIT')


def data_evidence(journal):
    """Never find a latest run: every receipt is bound to the reserved output."""
    D, _, E = modules()
    output = output_path('data-bear', journal['data']['attempt_token'])
    private_directory(output)
    result, sha = read_json(output / 'result.json')
    require(type(result) is dict and result.get('evidence_path') == str(output), 'DATA_RECEIPT_IDENTITY_MISMATCH')
    require(journal['data']['result_sha256'] in (None, sha), 'DATA_RECEIPT_CHANGED')
    require(all(result.get(key) == value for key, value in journal['input'].items()), 'DATA_INPUT_MISMATCH')
    outcome = result.get('result')
    require(type(outcome) is str and outcome in {'passed', 'unsupported', 'failed', 'interrupted'}, 'DATA_OUTCOME_UNKNOWN')
    if outcome in {'failed', 'interrupted'}:
        return result, sha, None, None
    require(result.get('checkpoint') == 'completed-in-memory' and result.get('cloud_calls') == 0
            and result.get('retained_data_mutation_authorized') is False, 'DATA_NOT_VERIFIED')
    case_id = journal['input']['case_id']
    plan = D.selected_plan(result.get('plan'), case_id)
    if outcome == 'unsupported':
        require(plan['operation'] == 'unsupported' and result.get('adapter_evidence') is None
                and result.get('verification') == {'dataops_started': False, 'materializations': 0, 'plan': plan,
                    'reason': 'unsupported-request', 'result': 'not-executed'}
                and not (output / 'adapter-stdout.json').exists()
                and not (output / 'adapter-stdout.json').is_symlink(), 'UNSUPPORTED_EXECUTION_MISMATCH')
        audit_receipt(result, ['llm.data_plan', 'bear.data.assess'], tool=True, blocked=True)
        return result, sha, None, None
    audit_receipt(result, ['llm.data_plan', 'bear.data.materialize', 'bear.data.assess'], tool=True)
    adapter, adapter_sha = read_json(output / 'adapter-stdout.json')
    D.verify_adapter_receipt(adapter)
    verification = D.verify_materialization(adapter, plan, case_id=case_id)
    require(result.get('verification') == verification and result.get('adapter_evidence') == verification['evidence'],
            'DATA_VERIFICATION_MISMATCH')
    source_path = output_path('data-plans', verification['run_token'])
    projection = E.validate_projection(E.read_evidence(source_path))
    require(projection['run_token'] == verification['run_token'] and projection['operation'] == plan['operation'],
            'DATA_SOURCE_MISMATCH')
    source = {'run_token': projection['run_token'], 'operation': projection['operation'],
              'evidence_sha256': digest(projection), 'adapter_sha256': adapter_sha}
    require(journal['source'] in (None, source), 'DATA_SOURCE_CHANGED')
    require(read_json(output / 'result.json')[1] == sha and read_json(output / 'adapter-stdout.json')[1] == adapter_sha,
            'DATA_RECEIPT_CHANGED')
    return result, sha, source, projection


def catalog_files(attempt):
    output = output_path('catalog-bear', attempt['attempt_token'])
    if not output.exists():
        return {}
    private_directory(output)
    names = [path.name for path in output.iterdir()]
    require(set(names) <= CATALOG_FILES, 'UNEXPECTED_CATALOG_FILE')
    return {name: hashlib.sha256(read(output / name)).hexdigest() for name in sorted(names)}


def catalog_evidence(journal, attempt, projection):
    _, C, E = modules()
    output = output_path('catalog-bear', attempt['attempt_token'])
    result, sha = read_json(output / 'result.json')
    require(type(result) is dict and result.get('source_run_token') == journal['source']['run_token']
            and result.get('evidence_path') == str(output), 'CATALOG_RECEIPT_IDENTITY_MISMATCH')
    require(attempt['result_sha256'] in (None, sha), 'CATALOG_RECEIPT_CHANGED')
    outcome = result.get('result')
    require(type(outcome) is str and outcome in {'passed', 'failed', 'interrupted'}, 'CATALOG_OUTCOME_UNKNOWN')
    if outcome != 'passed':
        return result, sha
    require(result.get('checkpoint') == 'completed-in-memory' and result.get('cloud_calls') == 0,
            'CATALOG_NOT_VERIFIED')
    audit_receipt(result, ['bear.catalog.observe', 'llm.catalog_plan', 'bear.catalog.apply', 'bear.catalog.repeat', 'bear.catalog.assess'])
    source, _ = read_json(output / 'source-evidence.json')
    require(E.validate_projection(source) == projection, 'CATALOG_SOURCE_MISMATCH')
    state, _ = read_json(output / 'catalog-observation.json')
    observation = C.validate_observation({'projection': projection, 'catalog_state': state})
    C.validate_plan(result.get('plan'), observation['evidence'], executable=True)
    applied, _ = read_json(output / 'apply.json')
    repeated, _ = read_json(output / 'repeat.json')
    require(applied == result.get('applied') and repeated == result.get('repeated'), 'CATALOG_RECEIPT_MISMATCH')
    first = C.verify_receipt(applied, observation)
    second = C.verify_receipt(repeated, observation, previous=first)
    wanted = {'result': 'passed', 'run_token': projection['run_token'], 'tables': 2, 'lineage_edges': 2,
              'repeat_noop': True, 'table_ids': second['table_ids'], 'historical_models_removed_from_trino': True}
    require(result.get('verification') == wanted and result.get('saved_otel_spans_verified') == 5,
            'CATALOG_VERIFICATION_MISMATCH')
    read(output / 'spans.jsonl')
    C.verify_saved_spans(output / 'spans.jsonl', result['audit_records'], result['trace_id'])
    require(read_json(output / 'result.json')[1] == sha, 'CATALOG_RECEIPT_CHANGED')
    return result, sha


def response(journal, *, replayed=False):
    phase = journal['phase']
    return {'schema_version': 1, 'handoff_token': journal['handoff_token'],
            'result': 'passed' if phase == 'completed' else 'unsupported' if phase == 'unsupported'
                      else 'failed' if phase in {'failed', 'catalog_failed'} else 'unknown',
            'phase': phase, 'data_attempt_token': journal['data']['attempt_token'],
            'data_plan_token': journal['source']['run_token'] if journal['source'] else None,
            'catalog_attempts': len(journal['catalog_attempts']), 'replayed': replayed,
            'error_code': journal['error_code'], 'evidence': 'tmp/local-experiment/data-catalog/' + journal['handoff_token'],
            'limits': list(LIMITS)}


async def run_handoff(config, handoff_token, *, case_id=None, request=None, resume=False,
                      data_runner=None, catalog_runner=None):
    D, C, _ = modules()
    token(handoff_token)
    require(type(resume) is bool and (not resume or case_id is None and request is None), 'INVALID_RESUME_INPUT')
    D.validate_config(config)
    require(config['active_provider'] == 'local', 'LOCAL_PROVIDER_REQUIRED')
    metadata = None if resume else D.request_metadata(D.request_spec(case_id=case_id, request=request))
    data_runner, catalog_runner = data_runner or D.run, catalog_runner or C.run
    with locked(handoff_token, resume=resume) as (directory, fresh):
        if fresh:
            journal = {'schema_version': 1, 'handoff_token': handoff_token, 'created_at': timestamp(), 'updated_at': timestamp(),
                       'input': metadata, 'config_sha256': digest(config), 'phase': 'data_pending',
                       'data': {'attempt_token': reserve_output('data-bear'), 'outcome': 'pending', 'result_sha256': None},
                       'source': None, 'catalog_attempts': [], 'error_code': None, 'limits': list(LIMITS)}
            persist(directory, journal)  # Reservation before the only permitted Data invocation.
        else:
            journal = validate_journal(read_json(directory / 'handoff.json')[0], handoff_token)
            require(metadata is None or metadata == journal['input'], 'HANDOFF_INPUT_CONFLICT')

        def save():
            journal['updated_at'] = timestamp()
            persist(directory, journal)

        active = 'data'
        try:
            if fresh:
                returned = await data_runner(config, output_path('data-bear', journal['data']['attempt_token']),
                                             case_id=case_id, request=request)
                saved, _ = read_json(output_path('data-bear', journal['data']['attempt_token']) / 'result.json')
                require(returned == saved, 'DATA_RETURN_MISMATCH')
            # A restart only inspects this reserved Data attempt. Even a missing
            # result does not permit another invocation or adoption of a latest run.
            data, sha, source, projection = data_evidence(journal)
            journal['data'].update(outcome=data['result'], result_sha256=sha)
            if data['result'] in {'failed', 'interrupted', 'unsupported'}:
                journal['phase'] = 'unsupported' if data['result'] == 'unsupported' else 'failed'
                journal['error_code'] = None if data['result'] == 'unsupported' else 'DATA_DID_NOT_PASS'
                if fresh or journal['phase'] not in {'unsupported', 'failed'}:
                    save()
                elif not fresh:
                    # Persist recovery of an unsealed final child receipt.
                    original, _ = read_json(directory / 'handoff.json')
                    if original != journal:
                        save()
                return response(journal, replayed=not fresh)
            journal['source'] = source
            active = 'catalog'
            for attempt in journal['catalog_attempts']:
                if attempt['files'] is not None:
                    require(catalog_files(attempt) == attempt['files'], 'CATALOG_ATTEMPT_CHANGED')
            if journal['catalog_attempts']:
                latest = journal['catalog_attempts'][-1]
                try:
                    result, sha = catalog_evidence(journal, latest, projection)
                except (OSError, HandoffError, ValueError):
                    if latest['outcome'] == 'passed' or journal['phase'] == 'completed':
                        raise HandoffError('COMPLETED_EVIDENCE_INVALID') from None
                    result, sha = None, None
                if result is not None and result['result'] == 'passed':
                    latest.update(outcome='passed', result_sha256=sha, files=catalog_files(latest))
                    if journal['phase'] != 'completed':
                        journal.update(phase='completed', error_code=None)
                        save()
                    return response(journal, replayed=True)
                if latest['files'] is None:
                    latest.update(outcome=result['result'] if result else 'unknown', result_sha256=sha,
                                  files=catalog_files(latest))
            require(journal['config_sha256'] == digest(config), 'HANDOFF_CONFIG_CHANGED')
            require(len(journal['catalog_attempts']) < MAX_ATTEMPTS, 'CATALOG_ATTEMPT_LIMIT')
            # Revalidate the fixed source at each handoff, including a restart.
            _, _, source_again, projection_again = data_evidence(journal)
            require(source_again == source and projection_again == projection, 'DATA_SOURCE_CHANGED')
            journal.update(phase='data_verified', error_code=None)
            save()
            attempt = {'attempt_token': reserve_output('catalog-bear'), 'outcome': 'pending', 'result_sha256': None, 'files': None}
            journal['catalog_attempts'].append(attempt)
            journal['phase'] = 'catalog_pending'
            save()  # Durable attempt intent before any Catalog model or API call.
            returned = await catalog_runner(config, source['run_token'], output_path('catalog-bear', attempt['attempt_token']))
            saved, _ = read_json(output_path('catalog-bear', attempt['attempt_token']) / 'result.json')
            require(returned == saved, 'CATALOG_RETURN_MISMATCH')
            result, sha = catalog_evidence(journal, attempt, projection)
            _, _, final_source, _ = data_evidence(journal)
            require(final_source == source, 'DATA_SOURCE_CHANGED')
            attempt.update(outcome=result['result'], result_sha256=sha, files=catalog_files(attempt))
            journal.update(phase='completed' if result['result'] == 'passed' else 'catalog_failed',
                           error_code=None if result['result'] == 'passed' else 'CATALOG_DID_NOT_PASS')
            save()
            return response(journal)
        except BaseException as error:
            code = error.code if isinstance(error, HandoffError) else ('INTERRUPTED' if not isinstance(error, Exception) else 'HANDOFF_EVIDENCE_OR_CHILD_FAILED')
            journal.update(phase='unknown', error_code=code)
            attempt = journal['data'] if active == 'data' else (journal['catalog_attempts'][-1] if journal['catalog_attempts'] else None)
            if attempt is not None and attempt['outcome'] == 'pending':
                attempt['outcome'] = 'interrupted' if not isinstance(error, Exception) else 'unknown'
            # Never overwrite the child's result/progress, or hide the original
            # interruption behind a secondary local journal failure.
            try:
                save()
            except BaseException:
                pass
            if not isinstance(error, Exception):
                raise
            return response(journal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--handoff-token')
    inputs = run.add_mutually_exclusive_group()
    inputs.add_argument('--case', choices=('uppercase_names_ja', 'name_lengths_ja', 'row_count_ja'))
    inputs.add_argument('--request-stdin', action='store_true')
    resume_parser = commands.add_parser('resume')
    resume_parser.add_argument('--handoff-token', required=True)
    args = parser.parse_args()
    handoff_token = args.handoff_token or uuid.uuid4().hex
    try:
        token(handoff_token)
        D, _, _ = modules()
        request = None
        if args.command == 'run' and args.request_stdin:
            raw = sys.stdin.buffer.read(4097)
            require(len(raw) <= 4096, 'REQUEST_TOO_LARGE')
            request = raw.decode('utf-8').removesuffix('\n').removesuffix('\r')
        if args.command == 'run':
            D.request_spec(case_id=args.case, request=request)
        print(canonical({'handoff_token': handoff_token, 'command': args.command}), flush=True)
        with D.SESSION.interruption_handlers():
            report = asyncio.run(run_handoff(D.load_config(LOCAL / 'llm/providers.qwen-local.json'), handoff_token,
                                            case_id=args.case if args.command == 'run' else None, request=request,
                                            resume=args.command == 'resume'))
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        report = {'result': 'unknown', 'handoff_token': handoff_token,
                  'error_code': error.code if isinstance(error, HandoffError) else 'HANDOFF_SETUP_OR_INTERRUPTION'}
        print(canonical(report))
        if hasattr(error, 'signum'):
            return 128 + error.signum
        if not isinstance(error, Exception):
            raise
        return 1
    print(canonical(report))
    return 0 if report['result'] == 'passed' else 2 if report['result'] == 'unsupported' else 1


if __name__ == '__main__':
    raise SystemExit(main())
