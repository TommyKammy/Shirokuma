"""Pure selection proposal boundary; no model, Reader, filesystem or SQL calls.

An accepted proposal is a bounded current-head Reader request, not proof that
the model understood the natural language. The owner separately preserves the
describe receipt and verifies the executed table UUID/schema after the read.
"""
from __future__ import annotations

from copy import deepcopy
import json
import unicodedata

from tools.local_experiment.notebook import selection as S
from tools.local_experiment.query_read import contract as R

MAX_INPUT_BYTES = 4096
MAX_REQUEST_CHARS = 512
MAX_REQUEST_BYTES = 2048
MAX_CONTEXT_COLUMNS = 32
MAX_MODEL_BODY_BYTES = 8192
ERROR_CODES = frozenset({'NOTEBOOK_ASSIST_INPUT_INVALID', 'NOTEBOOK_ASSIST_CONTEXT_INVALID',
                         'NOTEBOOK_ASSIST_DESCRIBE_INVALID', 'NOTEBOOK_ASSIST_PROPOSAL_INVALID'})


class AssistError(ValueError):
    """Fixed code only; caller requests and model bodies are never error text."""
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else 'NOTEBOOK_ASSIST_INPUT_INVALID'
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise AssistError(code)


def normalize_input(value):
    code = 'NOTEBOOK_ASSIST_INPUT_INVALID'
    require(type(value) is dict and set(value) == {'table', 'request'}, code)
    try:
        table = R.normalize_request({'command':'describe', 'table':value['table']})['table']
    except (R.ContractError, TypeError, ValueError):
        raise AssistError(code) from None
    request = value['request']
    require(type(request) is str and 1 <= len(request) <= MAX_REQUEST_CHARS and bool(request.strip()), code)
    require(all(unicodedata.category(char) not in {'Cc','Cf','Cs','Zl','Zp'} for char in request), code)
    require(len(request.encode('utf-8')) <= MAX_REQUEST_BYTES, code)
    normalized = {'table':table, 'request':request}
    require(len(json.dumps(normalized, ensure_ascii=False).encode('utf-8')) <= MAX_INPUT_BYTES, code)
    return normalized


def parse_input(raw):
    try:
        value = R.parse_json(raw, MAX_INPUT_BYTES)
    except R.ContractError:
        raise AssistError('NOTEBOOK_ASSIST_INPUT_INVALID') from None
    return normalize_input(value)


def normalize_context(value):
    """Validate only the schema projection permitted in the model request."""
    code = 'NOTEBOOK_ASSIST_CONTEXT_INVALID'
    require(type(value) is dict and set(value) == {'table','request','columns'}, code)
    try:
        source = normalize_input({key:value[key] for key in ('table','request')})
    except AssistError:
        raise AssistError(code) from None
    columns = value['columns']
    require(type(columns) is list and 1 <= len(columns) <= MAX_CONTEXT_COLUMNS, code)
    names = []
    for column in columns:
        require(type(column) is dict and set(column) == {'name','type'}, code)
        require(type(column['name']) is str and R.IDENTIFIER.fullmatch(column['name']), code)
        try:
            require(R.scalar_type(column['type']) == column['type'], code)
        except (R.ContractError, TypeError, ValueError):
            raise AssistError(code) from None
        names.append(column['name'])
    require(len(set(names)) == len(names), code)
    return {**source, 'columns':deepcopy(columns)}


def model_context(value, describe_identity):
    """Project a validated describe identity without IDs, hashes or row data."""
    source = normalize_input(value)
    try:
        require(type(describe_identity) is dict, 'NOTEBOOK_ASSIST_DESCRIBE_INVALID')
        identity = R.validate_identity(describe_identity,
            {'command':'describe','table':source['table']}, describe_identity.get('columns'))
    except (R.ContractError, TypeError, ValueError):
        raise AssistError('NOTEBOOK_ASSIST_DESCRIBE_INVALID') from None
    return normalize_context({**source,
                              'columns':[{key:column[key] for key in ('name','type')}
                                         for column in identity['columns']]})


def validate_proposal(value, context):
    """Return a validated Reader selection, or None for explicit unsupported.

    An invalid/ambiguous structure is an error, never an empty selection. Model
    text is classified by the model; this validator enforces executable scope.
    """
    context = normalize_context(context)
    code = 'NOTEBOOK_ASSIST_PROPOSAL_INVALID'
    require(type(value) is dict and set(value) == {'columns','limit','unsupported'}
            and type(value['unsupported']) is bool, code)
    if value['unsupported']:
        require(value['columns'] is None and value['limit'] is None, code)
        return None
    try:
        request = S.normalize_request({'command':'select', 'table':context['table'],
                                       'columns':value['columns'], 'limit':value['limit']})
    except (S.SelectionError, TypeError, ValueError):
        raise AssistError(code) from None
    known = {column['name'] for column in context['columns']}
    require(all(name in known for name in request['columns']), code)
    return request
