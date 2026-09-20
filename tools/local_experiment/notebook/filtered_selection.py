"""Canonical current-head equality selection for one registered Notebook.

The existing query_filter contract compiles the predicate and validates typed
public rows. The literal remains in private input/Reader argv/Trino SQL;
public predicate metadata is only column/type/operator. Explicit rows may
contain a requested value. This adapter generates no SQL and offers no retry.
"""
from __future__ import annotations

import hashlib
import json

from tools.local_experiment.notebook import selection as S
from tools.local_experiment.query_filter import contract as F

PROFILE = 'filtered-table-notebook-v3'
MAX_REQUEST_BYTES = S.MAX_REQUEST_BYTES
REQUEST_FILENAME = S.REQUEST_FILENAME
REQUEST_PATH_ENV = S.REQUEST_PATH_ENV
REQUEST_SHA_ENV = S.REQUEST_SHA_ENV
SelectionError = S.SelectionError
ERROR_CODES = S.ERROR_CODES
TEMPLATE_PATH = 'examples/local_filtered_table_preview.ipynb'
TEMPLATE_SHA256 = 'bf625af01d3eda5ac1acbd4b2160a2c4bb49b6600c1110c4dc3ad1d87ebfa76d'
CODE_CELLS = (
    'from IPython.display import JSON, display\nfrom tools.local_experiment.notebook.client import read_requested_filtered_table, read_evaluations',
    'preview = read_requested_filtered_table()\ndisplay(JSON(preview))',
    'evaluations = read_evaluations()\ndisplay(JSON(evaluations))',
)


def normalize_request(value):
    try:
        value = F.normalize_request(value)
    except F.ContractError:
        raise SelectionError('NOTEBOOK_SELECTION_INVALID') from None
    S._require(value['snapshot_id'] is None, 'NOTEBOOK_SELECTION_INVALID')
    return value


def parse_request(raw):
    try:
        return normalize_request(F.parse_request(raw))
    except F.ContractError:
        raise SelectionError('NOTEBOOK_SELECTION_INVALID') from None


def canonical(request):
    return F.canonical(normalize_request(request)) + b'\n'


canonical_bytes = canonical


def digest(request):
    return hashlib.sha256(canonical(request)).hexdigest()


def arguments(binary, root, request):
    request = normalize_request(request)
    return S.arguments(binary, root, F.base_request(request)) + [
        '--where-column', request['where']['column'], '--equals', request['where']['literal']]


def load_request(path, expected_sha):
    return S._load_canonical_request(path, expected_sha, parse_request, canonical)


def validate_template(raw):
    code = 'NOTEBOOK_SELECTION_TEMPLATE_INVALID'
    S._require(type(raw) is bytes and 0 < len(raw) <= 32*1024
               and hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA256, code)
    try:
        value = json.loads(raw.decode('utf-8'))
        cells = value['cells']
        selected = [cell for cell in cells if cell['cell_type'] == 'code']
        S._require(value['nbformat'] == 4 and value['nbformat_minor'] == 5 and len(cells) == 6
                   and len(selected) == 3 and tuple(''.join(cell['source']) for cell in selected) == CODE_CELLS
                   and all(cell['outputs'] == [] and cell['execution_count'] is None for cell in selected), code)
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise SelectionError(code) from None
    return value
