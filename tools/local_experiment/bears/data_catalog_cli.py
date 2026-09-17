"""Strict JSONL bridge for the local Data→Catalog product CLI.

Only the allocated identity and a final safe projection use stdout. Raw stdin,
workflow output, child exceptions and absolute receipt paths are never echoed.
An allocated identity is not proof that a durable execution intent exists.
Status reads saved evidence only; it never calls run_handoff or changes a journal.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))  # Explicit fixed source paths also work with python -I.
sys.path.insert(0, str(ROOT))
import run_data_catalog as H

CASES = ('uppercase_names_ja', 'name_lengths_ja', 'row_count_ja')
ERROR_CODES = frozenset({'ARGUMENTS_INVALID', 'INPUT_INVALID', 'HANDOFF_TOKEN_INVALID',
    'ENVIRONMENT_NOT_READY', 'HANDOFF_NOT_FOUND', 'HANDOFF_BUSY', 'HANDOFF_JOURNAL_INVALID',
    'HANDOFF_EVIDENCE_INVALID', 'HANDOFF_CONFIG_CHANGED', 'CATALOG_ATTEMPT_LIMIT',
    'HANDOFF_FAILED', 'INTERRUPTED'})
RESULTS = frozenset({'passed', 'unsupported', 'failed', 'unknown', 'not_started', 'not_found', 'busy'})
FINAL_KEYS = frozenset({'schema_version', 'event', 'command', 'handoff_token', 'result', 'phase',
    'execution', 'verification', 'data_attempt_token', 'data_plan_token', 'catalog_attempts',
    'replayed', 'error_code', 'evidence'})


class CLIError(ValueError):
    def __init__(self, code):
        if code not in ERROR_CODES:
            code = 'HANDOFF_FAILED'
        self.code = code
        super().__init__(code)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal errors can include input argument values.
        raise CLIError('ARGUMENTS_INVALID')


class Discard(io.TextIOBase):
    """Do not retain arbitrary library stdout/stderr in memory or on disk."""
    def write(self, value):
        return len(value)

    def flush(self):
        pass


def token(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise CLIError('HANDOFF_TOKEN_INVALID')
    return value


def request_text(stream):
    raw = stream.read(4097)
    if type(raw) is not bytes or len(raw) > 4096:
        raise CLIError('INPUT_INVALID')
    try:
        text = raw.decode('utf-8').removesuffix('\n').removesuffix('\r')
    except UnicodeError:
        raise CLIError('INPUT_INVALID') from None
    if (not 1 <= len(text) <= 512 or not text.strip()
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
        raise CLIError('INPUT_INVALID')
    return text


def projection(command, handoff_token, *, result='unknown', execution='not-started',
               verification='not-checked', error_code=None, saved=None):
    value = {'schema_version': 1, 'event': 'result', 'command': command,
        'handoff_token': handoff_token, 'result': result, 'phase': None, 'execution': execution,
        'verification': verification, 'data_attempt_token': None, 'data_plan_token': None,
        'catalog_attempts': 0, 'replayed': False, 'error_code': error_code, 'evidence': None}
    if saved is not None:
        for key in ('phase', 'data_attempt_token', 'data_plan_token', 'catalog_attempts', 'replayed'):
            value[key] = saved[key]
        value['evidence'] = 'tmp/local-experiment/data-catalog/' + handoff_token
    return validate_projection(value)


def validate_projection(value):
    if (type(value) is not dict or set(value) != FINAL_KEYS or type(value['schema_version']) is not int
            or value['schema_version'] != 1 or value['event'] != 'result'
            or value['command'] not in {'run', 'resume', 'status'} or value['result'] not in RESULTS
            or value['execution'] not in {'not-started', 'invoked', 'read-only'}
            or value['verification'] not in {'not-checked', 'journal-only', 'saved-evidence'}
            or value['phase'] is not None and value['phase'] not in H.PHASES
            or type(value['catalog_attempts']) is not int or not 0 <= value['catalog_attempts'] <= H.MAX_ATTEMPTS
            or type(value['replayed']) is not bool
            or value['error_code'] is not None and value['error_code'] not in ERROR_CODES):
        raise CLIError('HANDOFF_EVIDENCE_INVALID')
    for key in ('handoff_token', 'data_attempt_token', 'data_plan_token'):
        if value[key] is not None:
            token(value[key])
    if value['evidence'] is not None and (value['handoff_token'] is None or value['evidence'] !=
            'tmp/local-experiment/data-catalog/' + value['handoff_token']):
        raise CLIError('HANDOFF_EVIDENCE_INVALID')
    if ((value['command'] == 'status') != (value['execution'] == 'read-only')
            or value['result'] == 'passed' and (value['phase'] != 'completed'
                or value['verification'] != 'saved-evidence' or value['catalog_attempts'] < 1
                or value['data_attempt_token'] is None or value['data_plan_token'] is None)
            or value['result'] == 'unsupported' and (value['phase'] != 'unsupported'
                or value['verification'] != 'saved-evidence' or value['catalog_attempts'] != 0
                or value['data_attempt_token'] is None or value['data_plan_token'] is not None)
            or value['result'] == 'failed' and value['phase'] not in {'failed', 'catalog_failed'}):
        raise CLIError('HANDOFF_EVIDENCE_INVALID')
    return value


def safe_error(error, fallback='HANDOFF_FAILED'):
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    code = getattr(error, 'code', None)
    if code in ERROR_CODES:
        return code
    if code == 'INVALID_HANDOFF_IDENTITY':
        return 'HANDOFF_TOKEN_INVALID'
    return fallback


@contextmanager
def status_lock(directory):
    """Inspect an existing lock without creating it or changing file contents."""
    path = directory / 'handoff.lock'
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
        raise CLIError('HANDOFF_JOURNAL_INVALID')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise CLIError('HANDOFF_JOURNAL_INVALID')
        busy = False
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            busy = True
        yield busy
    finally:
        os.close(fd)


def inspect_handoff(handoff_token):
    """Return (safe projection, inspection exit code), with zero workflow calls."""
    token(handoff_token)
    root = H.STATE
    directory = root / handoff_token
    try:
        # A broken symlink is not an absent operation.
        if not root.exists() and not root.is_symlink():
            return projection('status', handoff_token, result='not_found', execution='read-only',
                              error_code='HANDOFF_NOT_FOUND'), 1
        H.private_directory(root)
        if not directory.exists() and not directory.is_symlink():
            return projection('status', handoff_token, result='not_found', execution='read-only',
                              error_code='HANDOFF_NOT_FOUND'), 1
        H.private_directory(directory)
        with status_lock(directory) as busy:
            journal, initial_hash = H.read_json(directory / 'handoff.json')
            H.validate_journal(journal, handoff_token)
            saved = H.response(journal, replayed=False)
            if busy:
                return projection('status', handoff_token, result='busy', execution='read-only',
                    verification='journal-only', error_code='HANDOFF_BUSY', saved=saved), 0
            verification = 'journal-only'
            if journal['phase'] in {'completed', 'unsupported', 'failed', 'catalog_failed'}:
                data, _, source, evidence = H.data_evidence(journal)
                for attempt in journal['catalog_attempts']:
                    if attempt['files'] is not None and H.catalog_files(attempt) != attempt['files']:
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                if journal['phase'] == 'completed':
                    if data['result'] != 'passed' or source != journal['source']:
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                    for attempt in journal['catalog_attempts']:
                        if attempt['files'] is None or H.catalog_files(attempt) != attempt['files']:
                            raise CLIError('HANDOFF_EVIDENCE_INVALID')
                    result, _ = H.catalog_evidence(journal, journal['catalog_attempts'][-1], evidence)
                    if result['result'] != 'passed':
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                elif journal['phase'] == 'unsupported':
                    if data['result'] != 'unsupported':
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                elif journal['phase'] == 'failed':
                    if data['result'] not in {'failed', 'interrupted'}:
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                else:
                    if data['result'] != 'passed' or source != journal['source'] or not journal['catalog_attempts']:
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                    latest = journal['catalog_attempts'][-1]
                    if latest['files'] is None or H.catalog_files(latest) != latest['files']:
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                    result, _ = H.catalog_evidence(journal, latest, evidence)
                    if result['result'] not in {'failed', 'interrupted'}:
                        raise CLIError('HANDOFF_EVIDENCE_INVALID')
                verification = 'saved-evidence'
            if H.read_json(directory / 'handoff.json')[1] != initial_hash:
                raise CLIError('HANDOFF_JOURNAL_INVALID')
            return projection('status', handoff_token, result=saved['result'], execution='read-only',
                verification=verification, error_code=None if saved['error_code'] is None else 'HANDOFF_FAILED', saved=saved), 0
    except BaseException as error:
        if not isinstance(error, Exception):
            raise
        code = safe_error(error, 'HANDOFF_JOURNAL_INVALID')
        # Completed evidence validation failures must never become a passed view.
        if type(locals().get('journal')) is dict and journal.get('phase') in {'completed', 'unsupported', 'failed', 'catalog_failed'}:
            code = 'HANDOFF_EVIDENCE_INVALID'
        if isinstance(error, ImportError):
            code = 'ENVIRONMENT_NOT_READY'
        return projection('status', handoff_token, execution='read-only', error_code=code), 1


def arguments(argv):
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    run = sub.add_parser('run')
    run.add_argument('--handoff-token', required=True)
    inputs = run.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--case', choices=CASES)
    inputs.add_argument('--request-stdin', action='store_true')
    for name in ('resume', 'status'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--handoff-token', required=True)
    return parser.parse_args(argv)


def _emit(stream, value):
    stream.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')
    stream.flush()


def main(argv=None, *, stdin=None, stdout=None):
    stream = stdout or sys.stdout
    source = stdin if stdin is not None else sys.stdin.buffer
    action, identity, invoked = 'run', None, False
    try:
        args = arguments(argv)
        action, identity = args.command, token(args.handoff_token)
        request = request_text(source) if action == 'run' and args.request_stdin else None
        if action == 'resume' and not (H.STATE / identity).exists():
            raise CLIError('HANDOFF_NOT_FOUND')
        # No imports, config loads or model setup are needed for this input gate.
        _emit(stream, {'schema_version': 1, 'event': 'allocated', 'command': action, 'handoff_token': identity})
        with redirect_stdout(Discard()), redirect_stderr(Discard()):
            if action == 'status':
                report, exit_code = inspect_handoff(identity)
            else:
                try:
                    D, _, _ = H.modules()
                    config = D.load_config(H.LOCAL / 'llm/providers.qwen-local.json')
                    D.validate_config(config)
                    if config['active_provider'] != 'local':
                        raise CLIError('ENVIRONMENT_NOT_READY')
                    if action == 'run':
                        D.request_spec(case_id=args.case, request=request)
                except Exception:
                    raise CLIError('ENVIRONMENT_NOT_READY') from None
                with D.SESSION.interruption_handlers():
                    invoked = True
                    saved = asyncio.run(H.run_handoff(config, identity,
                        case_id=args.case if action == 'run' else None, request=request, resume=action == 'resume'))
                if (type(saved) is not dict or saved.get('handoff_token') != identity
                        or saved.get('result') not in {'passed', 'unsupported', 'failed', 'unknown'}):
                    raise CLIError('HANDOFF_EVIDENCE_INVALID')
                report = projection(action, identity, result=saved['result'], execution='invoked',
                    verification='saved-evidence' if saved['result'] in {'passed', 'unsupported', 'failed'} else 'journal-only',
                    error_code=None if saved.get('error_code') is None else
                        saved['error_code'] if saved['error_code'] in ERROR_CODES else 'HANDOFF_FAILED', saved=saved)
                exit_code = 0 if saved['result'] == 'passed' else 2 if saved['result'] == 'unsupported' else 1
        _emit(stream, report)
        return exit_code
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        report = projection(action, identity, result='unknown' if invoked or action == 'status' else 'not_started',
            execution='read-only' if action == 'status' else 'invoked' if invoked else 'not-started',
            error_code=safe_error(error, 'ENVIRONMENT_NOT_READY' if not invoked else 'HANDOFF_FAILED'))
        _emit(stream, report)
        if not isinstance(error, Exception):
            return 128 + getattr(error, 'signum', 2)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
