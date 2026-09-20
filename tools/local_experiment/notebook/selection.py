"""A private canonical current-head Reader selection for the fixed notebook.

Syntax validation does not establish table/column existence. The existing
Reader performs discovery, schema/snapshot binding, typed row projection and
cleanup. This adapter adds no SQL, historical snapshot, predicate or retry.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from tools.local_experiment.query_read import contract as C

MAX_REQUEST_BYTES = C.MAX_INPUT_BYTES
REQUEST_FILENAME = 'request.json'
REQUEST_PATH_ENV = 'SHIROKUMA_NOTEBOOK_REQUEST_PATH'
REQUEST_SHA_ENV = 'SHIROKUMA_NOTEBOOK_REQUEST_SHA256'
TEMPLATE_PATH = 'examples/local_table_preview.ipynb'
TEMPLATE_SHA256 = 'a2d030cf280aa3cb8d91b36b987ad9b8cddf6002868926f81f1f4150fb08162a'
CODE_CELLS = (
    'from IPython.display import JSON, display\nfrom tools.local_experiment.notebook.client import read_requested_table, read_evaluations',
    'preview = read_requested_table()\ndisplay(JSON(preview))',
    'evaluations = read_evaluations()\ndisplay(JSON(evaluations))',
)
ERROR_CODES = frozenset({'NOTEBOOK_SELECTION_INVALID', 'NOTEBOOK_SELECTION_FILE_INVALID',
                        'NOTEBOOK_SELECTION_CHANGED', 'NOTEBOOK_SELECTION_HASH_INVALID',
                        'NOTEBOOK_SELECTION_NONCANONICAL', 'NOTEBOOK_SELECTION_ARGUMENTS_INVALID',
                        'NOTEBOOK_SELECTION_TEMPLATE_INVALID'})
SHA = re.compile(r'[0-9a-f]{64}\Z')


class SelectionError(ValueError):
    """Fixed code only; caller values and underlying paths are not exposed."""
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else 'NOTEBOOK_SELECTION_INVALID'
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise SelectionError(code)


def normalize_request(value):
    try:
        value = C.normalize_request(value)
    except C.ContractError:
        raise SelectionError('NOTEBOOK_SELECTION_INVALID') from None
    _require(value['command'] == 'select', 'NOTEBOOK_SELECTION_INVALID')
    return value


def parse_request(raw):
    try:
        value = C.parse_request(raw)
    except C.ContractError:
        raise SelectionError('NOTEBOOK_SELECTION_INVALID') from None
    return normalize_request(value)


def canonical(request):
    return C.canonical(normalize_request(request)) + b'\n'


canonical_bytes = canonical


def digest(request):
    return hashlib.sha256(canonical(request)).hexdigest()


def validate_template(raw):
    """Return only the registered empty-output preview notebook, with no I/O."""
    code = 'NOTEBOOK_SELECTION_TEMPLATE_INVALID'
    _require(type(raw) is bytes and 0 < len(raw) <= 32 * 1024
             and hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA256, code)
    try:
        value = json.loads(raw.decode('utf-8'))
        cells = value['cells']
        code_cells = [cell for cell in cells if cell['cell_type'] == 'code']
        _require(value['nbformat'] == 4 and value['nbformat_minor'] == 5 and len(cells) == 6
                 and len(code_cells) == 3 and tuple(''.join(cell['source']) for cell in code_cells) == CODE_CELLS
                 and all(cell['outputs'] == [] and cell['execution_count'] is None for cell in code_cells), code)
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise SelectionError(code) from None
    return value


def _absolute_path(value, code):
    _require(isinstance(value, (str, Path)), code)
    text = str(value)
    _require(0 < len(text) <= 4096 and all(ord(char) >= 32 and ord(char) != 127 for char in text), code)
    path = Path(text)
    _require(path.is_absolute() and '..' not in path.parts, code)
    return path


def arguments(binary, root, request):
    request = normalize_request(request)
    binary = _absolute_path(binary, 'NOTEBOOK_SELECTION_ARGUMENTS_INVALID')
    root = _absolute_path(root, 'NOTEBOOK_SELECTION_ARGUMENTS_INVALID')
    return [str(binary), '--repo-root', str(root), '--profile', 'local-experiment',
            'query', 'select', '--table', request['table'], '--columns',
            ','.join(request['columns']), '--limit', str(request['limit'])]


def _signature(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)


def _file(value):
    _require(stat.S_ISREG(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o600
             and value.st_uid == os.getuid() and value.st_nlink == 1
             and 0 < value.st_size <= MAX_REQUEST_BYTES, 'NOTEBOOK_SELECTION_FILE_INVALID')


def _parents(path):
    values = []
    for parent in path.parents:
        info = parent.lstat()
        _require(stat.S_ISDIR(info.st_mode), 'NOTEBOOK_SELECTION_FILE_INVALID')
        # Ordinary sibling writes need not invalidate an immutable request file.
        values.append((str(parent), info.st_dev, info.st_ino, info.st_mode, info.st_uid))
    return values


def _retain(first, later):
    if first is None or isinstance(first, Exception) and not isinstance(later, Exception):
        return later
    return first


def _load_canonical_request(path, expected_sha, parser, encoder):
    """Read one stable private canonical file without following symlinks.

    Both the exact bytes and the canonical selected request are bound to the
    expected SHA. A changed/replaced file is not retried. Closing the owned fd
    cannot replace the first cancellation with a secondary ordinary error.
    """
    _require(type(expected_sha) is str and SHA.fullmatch(expected_sha), 'NOTEBOOK_SELECTION_HASH_INVALID')
    path = _absolute_path(path, 'NOTEBOOK_SELECTION_FILE_INVALID')
    fd, original, raw = None, None, b''
    try:
        parents = _parents(path)
        before = path.lstat()
        _file(before)
        # NONBLOCK also prevents a racing FIFO replacement from blocking open.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(fd)
        _file(opened)
        _require(_signature(opened) == _signature(before), 'NOTEBOOK_SELECTION_CHANGED')
        chunks, size = [], 0
        while size <= MAX_REQUEST_BYTES:
            chunk = os.read(fd, MAX_REQUEST_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b''.join(chunks)
        _require(len(raw) <= MAX_REQUEST_BYTES and _signature(os.fstat(fd)) == _signature(before),
                 'NOTEBOOK_SELECTION_CHANGED')
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                original = _retain(original, error)
    if original is not None:
        if isinstance(original, SelectionError) or not isinstance(original, Exception):
            raise original
        raise SelectionError('NOTEBOOK_SELECTION_FILE_INVALID') from None
    try:
        _require(_signature(path.lstat()) == _signature(before) and _parents(path) == parents,
                 'NOTEBOOK_SELECTION_CHANGED')
    except OSError:
        raise SelectionError('NOTEBOOK_SELECTION_CHANGED') from None
    _require(hashlib.sha256(raw).hexdigest() == expected_sha, 'NOTEBOOK_SELECTION_CHANGED')
    request = parser(raw)
    _require(encoder(request) == raw, 'NOTEBOOK_SELECTION_NONCANONICAL')
    return request


def load_request(path, expected_sha):
    return _load_canonical_request(path, expected_sha, parse_request, canonical)
