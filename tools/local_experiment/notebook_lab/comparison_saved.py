"""Cold read of a completed private comparison, with no process inspection.

Saved process identities describe the completed attempt. A PID reused today is
not evidence about that historical worker, so no live PID/PGID check is made.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import stat

from tools.local_experiment.notebook_lab import compare as B

C, N, T = B.C, B.N, B.C.T
LAB_STATE = B.LAB_STATE
STATE = LAB_STATE / 'comparisons'
FILES = {'input.json': C.MAX_INPUT_BYTES, 'intent.json': 4096, 'status.json': 4096,
         'launch.json': 4096, 'worker.stdout.json': C.MAX_PUBLIC_BYTES,
         'result.json': C.MAX_PUBLIC_BYTES + 4096}


class SavedComparisonError(ValueError):
    """Fixed error codes, without saved private data or paths."""


def require(condition):
    if not condition:
        raise SavedComparisonError('SAVED_COMPARISON_INVALID')


def normalize_token(token):
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token) is not None)
    return token


def _parents(path):
    require(path.is_absolute() and path.parent.parent == STATE)
    result = []
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode))
        if parent == LAB_STATE or parent.is_relative_to(LAB_STATE):
            require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid())
        result.append((str(parent), info.st_dev, info.st_ino, info.st_mode, info.st_uid))
    return result


def _read(path, maximum):
    # Same stable-file pattern as the existing saved training reader, bound to
    # comparisons instead of its MLflow-only directory.
    before, parents = path.lstat(), _parents(path)
    T._file(before, maximum)
    fd, first, raw = None, None, b''
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(fd)
        T._file(opened, maximum)
        require(T._signature(opened) == T._signature(before))
        chunks, size = [], 0
        while size <= maximum:
            chunk = os.read(fd, maximum + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b''.join(chunks)
        require(len(raw) <= maximum and T._signature(os.fstat(fd)) == T._signature(before))
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                first = T._keep(first, error)
    if first is not None:
        raise first
    require(T._signature(path.lstat()) == T._signature(before) and _parents(path) == parents)
    return raw, (T._signature(before), parents)


def _validate(raw, token, directory):
    request = C.parse_request(raw['input.json'])
    require(raw['input.json'] == N._canonical(request))
    intent = dict(schema_version=1, profile=B.PROFILE, argv=B.arguments(directory), request_sha256=C.digest(request))
    require(raw['intent.json'] == N._canonical(intent))
    status = N._parse(raw['status.json'])
    require(type(status) is dict and set(status) == B.T.STATUS_KEYS
            and type(status['schema_version']) is int and status['schema_version'] == 1
            and status['state'] == 'passed' and status['child_exited'] is True
            and status['cleanup_verified'] is True and type(status['exit_code']) is int
            and status['exit_code'] == 0 and status['error_code'] is None
            and status['evidence_token'] == token
            and all(type(status[k]) is int and status[k] > 1 for k in ('pid', 'pgid'))
            and status['pid'] == status['pgid'] and type(status['process_start']) is str
            and 1 <= len(status['process_start']) <= 64 and '\n' not in status['process_start'])
    require(raw['status.json'] == N._canonical(status))
    launch = N._parse(raw['launch.json'])
    require(type(launch) is dict and set(launch) == {'pid', 'ppid', 'pgid', 'process_start'}
            and type(launch['ppid']) is int and launch['ppid'] > 1)
    expected_launch = dict(pid=status['pid'], ppid=launch['ppid'], pgid=status['pgid'], process_start=status['process_start'])
    require(raw['launch.json'] == N._canonical(expected_launch))
    output = raw['worker.stdout.json']
    require(output.endswith(b'\n') and len(output.splitlines()) == 1)
    comparison = C.validate_result(N._parse(output), request)
    require(output == N._canonical(comparison))
    expected = dict(schema_version=1, profile=B.PROFILE, result='passed', attempt_token=token,
                    cleanup_verified=True, comparison=comparison)
    require(raw['result.json'] == N._canonical(expected))
    return expected


def load_saved(token):
    """Load only a passed, cleanup-confirmed snapshot; never fit or launch."""
    token = normalize_token(token)
    directory = STATE / token
    saved, first, public = {}, None, None
    try:
        for name, maximum in FILES.items():
            path = directory / name
            raw, signature = _read(path, maximum)
            saved[path] = raw, signature, maximum
        public = _validate({path.name: item[0] for path, item in saved.items()}, token, directory)
    except BaseException as error:
        first = error
    finally:
        for path, (raw, signature, maximum) in saved.items():
            try:
                current, current_signature = _read(path, maximum)
                require(current == raw and current_signature == signature)
            except BaseException as error:
                first = T._keep(first, error)
    if first is not None:
        if not isinstance(first, Exception):
            raise first
        raise SavedComparisonError('SAVED_COMPARISON_INVALID') from None
    return deepcopy(public)
