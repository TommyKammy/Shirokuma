#!/usr/bin/env python3
"""Describe, propose one bounded selection, then materialize registered assets.

The model receives column names/types and the caller's text, never table rows.
All child reads keep their own cleanup receipts. Planning and reading occur at
different times; table UUID/schema must agree, while a newer current head is
allowed. No arbitrary code, SQL, retry, scheduler or Notebook kernel is used.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import client as N
from tools.local_experiment.notebook import selection as S
from tools.local_experiment.notebook_assets import run as A
from tools.local_experiment.notebook_assist import contract as C

STATE = ROOT / 'tmp/local-experiment/notebook-assist'
PROFILE = 'natural-language-notebook-preview-v1'
DESCRIBE = 'bear.notebook_preview.describe'
INFER = 'llm.notebook_preview'
MATERIALIZE = 'bear.notebook_preview.materialize'
ERROR_CODES = C.ERROR_CODES | frozenset({
    'NOTEBOOK_ASSIST_ENVIRONMENT_INVALID', 'NOTEBOOK_ASSIST_SOURCE_CHANGED',
    'NOTEBOOK_ASSIST_INPUT_CHANGED', 'NOTEBOOK_ASSIST_DESCRIBE_FAILED',
    'NOTEBOOK_ASSIST_MODEL_FAILED', 'NOTEBOOK_ASSIST_AUDIT_FAILED',
    'NOTEBOOK_ASSIST_ASSETS_FAILED', 'NOTEBOOK_ASSIST_IDENTITY_CHANGED',
    'NOTEBOOK_ASSIST_FAILED', 'INTERRUPTED'})


class AssistRunError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise AssistRunError(code)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def error_code(error):
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    return str(error) if isinstance(error, (AssistRunError, C.AssistError)) and str(error) in ERROR_CODES else 'NOTEBOOK_ASSIST_FAILED'


def sources():
    paths = [*Path(__file__).parent.glob('*.py')]
    paths += [ROOT / name for name in (
        'tools/local_experiment/notebook/client.py', 'tools/local_experiment/notebook/selection.py',
        'tools/local_experiment/notebook_assets/run.py', 'tools/local_experiment/llm/providers.py',
        'tools/local_experiment/llm/providers.qwen-local.json',
        'tools/local_experiment/dataops/pyproject.toml', 'tools/local_experiment/dataops/uv.lock',
        S.TEMPLATE_PATH, 'go.mod', 'go.sum')]
    from tools.local_experiment.query_read.run import sources as reader_sources
    value = reader_sources()
    value.update({str(path.relative_to(ROOT)):digest(path.read_bytes()) for path in paths})
    return value


def environment():
    from tools.local_experiment.notebook_assist.provider import NotebookAssistProvider
    from tools.local_experiment.llm.providers import load_config
    config = load_config(ROOT / 'tools/local_experiment/llm/providers.qwen-local.json')
    require(config['active_provider'] == 'local', 'NOTEBOOK_ASSIST_ENVIRONMENT_INVALID')
    profile = config['providers']['local']
    return deepcopy(profile), NotebookAssistProvider(profile)


@contextmanager
def reader_environment(output, binary):
    """Own all four client variables, including restoration during setup failure."""
    previous = {key:os.environ.get(key) for key in A.ENV_KEYS}
    original = None
    try:
        for key in A.ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(SHIROKUMA_NOTEBOOK_RUN_DIR=str(output), SHIROKUMA_NOTEBOOK_BINARY=str(binary))
        yield
    except BaseException as error:
        original = error
    finally:
        for key, value in previous.items():
            try:
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            except BaseException as error:
                original = A._keep(original, error)
    if original is not None:
        raise original


def describe_closed(output, table):
    """A matching saved client status and terminal frame, not exit alone."""
    names = ('reader-intent.json', 'reader-status.json', 'reader.stdout.jsonl')
    if not any((output / name).exists() or (output / name).is_symlink() for name in names):
        return True
    try:
        intent = N._parse(N._read(output / names[0], 16384))
        require(type(intent) is dict and type(intent.get('schema_version')) is int
                and intent == {'schema_version':1, 'command':'query describe',
                           'request':{'command':'describe', 'table':table}}, 'NOTEBOOK_ASSIST_DESCRIBE_FAILED')
        status = N._parse(N._read(output / names[1], 16384))
        require(type(status) is dict and set(status) == {'schema_version','state','pid','pgid','process_start',
                    'evidence_token','exit_code','child_exited','cleanup_verified','error_code'}
                and type(status['schema_version']) is int and status['schema_version'] == 1
                and status['state'] in ('passed','failed','interrupted')
                and type(status['pid']) is int and status['pid'] > 0
                and type(status['pgid']) is int and status['pgid'] == status['pid']
                and (status['process_start'] is None or (type(status['process_start']) is str
                     and 1 <= len(status['process_start']) <= 64 and '\n' not in status['process_start']))
                and status['child_exited'] is True and status['cleanup_verified'] is True
                and type(status['exit_code']) is int and status['exit_code'] in (0,1,130,143)
                and (status['state'] != 'passed' or status['exit_code'] == 0)
                and status['error_code'] == {'passed':None,'failed':'NOTEBOOK_READER_FAILED',
                                              'interrupted':'NOTEBOOK_INTERRUPTED'}[status['state']],
                'NOTEBOOK_ASSIST_DESCRIBE_FAILED')
        token, _, cleanup = N._describe_reader_frames(
            N._read(output / names[2], N.C.MAX_PUBLIC_BYTES*2+2), status['exit_code'], table)
        return cleanup is True and token == status['evidence_token']
    except Exception:
        return False


def execute(output, binary, request, *, environment_factory=environment, audit_factory=None,
            describe=None, materialize=None, source_factory=sources):
    """One attempt; dependencies are injectable for offline lifecycle tests."""
    request = C.normalize_input(request)
    output, binary = Path(output), Path(binary)
    A._private_directory(output)
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(), 'NOTEBOOK_ASSIST_ENVIRONMENT_INVALID')
    # An exclusive intent makes accidental same-directory execution impossible.
    binary_sha = digest(binary.read_bytes())
    N._write(output / 'execution-intent.json', {'schema_version':1, 'profile':PROFILE,
                'input_sha256':digest(N._canonical(request)), 'binary_sha256':binary_sha})
    N._write(output / 'input.json', request)
    report = dict(schema_version=1, profile=PROFILE, result='running', started_at=stamp(), finished_at=None,
        input_sha256=digest(N._canonical(request)), source_hashes=source_factory(), model_calls=0,
        cloud_calls=0, retries=0, selection=None, description=None, audit=None, asset_result=None,
        describe_cleanup_confirmed=False, asset_cleanup_confirmed=False, error_code=None)
    selected, original, audit = None, None, None
    describe_dir, asset_dir = output / 'describe', output / 'assets'

    def save():
        N._write(output / 'summary.json', report, replace=True)

    def unchanged():
        require(N._read(output / 'input.json', 16384) == N._canonical(request), 'NOTEBOOK_ASSIST_INPUT_CHANGED')
        require(source_factory() == report['source_hashes'], 'NOTEBOOK_ASSIST_SOURCE_CHANGED')
        require(not binary.is_symlink() and binary.is_file() and digest(binary.read_bytes()) == binary_sha,
                'NOTEBOOK_ASSIST_SOURCE_CHANGED')

    def progress(value):
        report['audit'] = deepcopy(value)
        save()

    try:
        save()
        profile, model = environment_factory()
        report['provider'], report['model'] = profile['provider'], profile['model']
        if audit_factory is None:
            from tools.local_experiment.notebook_assist.audit import NotebookAudit
            audit_factory = NotebookAudit
        audit = audit_factory(output.name, output, profile, progress)
        audit.start()

        def inspect():
            unchanged()
            describe_dir.mkdir(mode=0o700)
            with reader_environment(describe_dir, binary):
                identity = (describe or N.read_describe)(request['table'])
            C.model_context(request, identity)
            require(describe_closed(describe_dir, request['table']), 'NOTEBOOK_ASSIST_DESCRIBE_FAILED')
            return identity

        identity = audit.phase(DESCRIBE, inspect)
        report['description'] = deepcopy(identity)
        context = C.model_context(request, identity)
        N._write(output / 'model-context.json', context)
        # Validate the bounded request body before recording an inference attempt.
        model.request_body('notebook_preview', context)
        unchanged()

        def infer():
            unchanged()
            require(report['model_calls'] == 0, 'NOTEBOOK_ASSIST_MODEL_FAILED')
            report['model_calls'] = 1
            save()
            return model.complete('notebook_preview', context)

        def validate_response(response):
            # The audit phase has already retained measured usage at this point.
            N._write(output / 'model-response.json', response)
            return C.validate_proposal(response['content'], context)

        selected = audit.phase(INFER, infer, inference=True,
                               validate=validate_response)
        report['selection'] = deepcopy(selected)
        save()
        unchanged()
        if selected is None:
            report['result'] = 'unsupported'
            audit.finish(status='blocked')
        else:
            def preview():
                unchanged()
                A.export(asset_dir, request=selected)
                result = (materialize or A.execute)(asset_dir, binary, request=selected)
                report['asset_result'] = deepcopy(result)
                save()
                require(result['result'] == 'passed' and result['reader_cleanup_confirmed'] is True,
                        'NOTEBOOK_ASSIST_ASSETS_FAILED')
                _, public, cleanup = N._reader_frames(N._read(asset_dir / 'reader.stdout.jsonl',
                        N.C.MAX_PUBLIC_BYTES*2+2), 0, request=selected)
                require(cleanup is True and public is not None and public['table_uuid'] == identity['table_uuid']
                        and public['schema_sha256'] == identity['schema_sha256'], 'NOTEBOOK_ASSIST_IDENTITY_CHANGED')
                return result

            audit.phase(MATERIALIZE, preview)
            unchanged()
            audit.finish()
            report['result'] = 'passed'
    except BaseException as error:
        original = error
    finally:
        if audit is not None:
            try:
                audit.close(original)
            except BaseException as error:
                original = A._keep(original, error)
        try:
            unchanged()
        except BaseException as error:
            original = A._keep(original, error)
        try:
            report['describe_cleanup_confirmed'] = describe_closed(describe_dir, request['table'])
            report['asset_cleanup_confirmed'] = A.reader_closed(asset_dir, request=selected) if selected is not None else not asset_dir.exists()
            if asset_dir.exists() and report['asset_result'] is None:
                try:
                    report['asset_result'] = N._parse(N._read(asset_dir / 'result.json', 65536))
                except Exception:
                    pass
        except BaseException as error:
            original = A._keep(original, error)
        if not (report['describe_cleanup_confirmed'] and report['asset_cleanup_confirmed']):
            report['result'] = 'unknown'
            original = A._keep(original, AssistRunError('NOTEBOOK_ASSIST_FAILED'))
        elif original is not None:
            report['result'] = 'interrupted' if not isinstance(original, Exception) else 'failed'
        report.update(finished_at=stamp(), error_code=error_code(original) if original is not None else None)
        try:
            save()
        except BaseException as error:
            original = A._keep(original, error)
    if original is not None:
        if not isinstance(original, Exception):
            raise original
        raise AssistRunError(error_code(original)) from None
    return report


def main(argv=None, *, stdin=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        require(argv == ['--request-stdin'], 'NOTEBOOK_ASSIST_INPUT_INVALID')
        value = C.parse_input((sys.stdin.buffer if stdin is None else stdin).read(C.MAX_INPUT_BYTES + 1))
    except Exception:
        print(json.dumps({'result':'failed','error_code':'NOTEBOOK_ASSIST_INPUT_INVALID','evidence':None}))
        return 1
    os.umask(0o077)
    output, report, original = None, None, None
    try:
        with A._signals():
            STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
            A._private_directory(STATE)
            output = STATE / uuid.uuid4().hex
            output.mkdir(mode=0o700)
            binary = output / 'shirokuma'
            with (output / 'build.log').open('xb') as log:
                subprocess.run(['go','build','-o',str(binary),'./cmd/shirokuma'], cwd=ROOT,
                               stdout=log, stderr=log, check=True, timeout=60)
            report = execute(output, binary, value)
    except BaseException as error:
        original = error
        if output is not None:
            try:
                report = N._parse(N._read(output / 'summary.json', 256 * 1024))
            except BaseException as other:
                original = A._keep(original, other)
    result = report['result'] if report is not None else 'interrupted' if original is not None and not isinstance(original,Exception) else 'failed'
    closed = (report is not None and report['describe_cleanup_confirmed'] and report['asset_cleanup_confirmed'])
    if report is None and output is not None:
        # A build or initial-source failure can precede both owned Readers.
        closed = not any((output / directory).exists() for directory in ('describe','assets'))
    if original is not None:
        result = 'unknown' if not closed else 'interrupted' if not isinstance(original, Exception) else 'failed'
    owner = dict(schema_version=1, profile=PROFILE, result=result,
                 summary_result=report['result'] if report is not None else None,
                 error_code=error_code(original) if original is not None else None,
                 model_calls=report['model_calls'] if report is not None else 0,
                 reader_cleanup_confirmed=bool(closed))
    saved = False
    if output is not None:
        try:
            N._write(output / 'owner-result.json', owner)
            saved = True
        except BaseException as error:
            original = A._keep(original, error)
            result = 'unknown' if not closed else 'interrupted' if not isinstance(original, Exception) else 'failed'
    public = dict(result=result, error_code=error_code(original) if original is not None else None,
                  model_calls=owner['model_calls'], reader_cleanup_confirmed=bool(closed),
                  evidence=f'tmp/local-experiment/notebook-assist/{output.name}/owner-result.json' if saved else None)
    print(json.dumps(public))
    if original is not None and not isinstance(original, Exception):
        return 128 + original.signum if isinstance(original, A.AssetsInterrupted) else 130
    return 0 if result == 'passed' else 2 if result == 'unsupported' else 1


if __name__ == '__main__':
    raise SystemExit(main())
