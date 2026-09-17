"""Pure projection of one retained fixture's OpenMetadata records.

This profile describes catalog metadata, not current SQL existence. Even a
generated relation named in lineage has sql_presence='not-checked'. Lineage is
one hop, not whole-catalog coverage or verified execution provenance. Absent
lineage collections are invalid; explicit empty collections are observed zero.

Two observations must have identical safe projections, anchor revisions and
canonical hashes of all six API responses. This conservative check can refuse
changes to unpublished fields (for example href or lineage update metadata).
It detects observed drift, not a transactional snapshot or changes that revert
between reads. Raw API objects and credentials are never returned or retained.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

LOCAL = Path(__file__).resolve().parents[1]
if str(LOCAL) not in sys.path:
    sys.path.insert(0, str(LOCAL))
from metadata import curate as K

SCHEMA_VERSION = 1
PROFILE = 'catalog-description-v1'
TABLE = 'smoke.fixture_v1'
QUALIFIED_TABLE = 'iceberg.' + TABLE
MAX_INPUT_BYTES = 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PUBLIC_BYTES = 64 * 1024
MAX_DESCRIPTION_BYTES = 2048
MAX_NODES = MAX_EDGES = 64
MAX_INT = 2**63 - 1
RAW_KEYS = frozenset({'team', 'service', 'database', 'schema', 'table', 'lineage'})
ANCHOR_NAMES = {'team': K.TEAM, 'service': K.SERVICE, 'database': K.DATABASE,
                'schema': K.SOURCE_SCHEMA, 'table': K.SOURCE}
PUBLIC_KEYS = frozenset({'schema_version', 'table', 'catalog_entity', 'description',
                         'owners', 'columns', 'lineage', 'sql_presence', 'observation'})
ERROR_CODES = frozenset({'INPUT_INVALID', 'REQUEST_INVALID', 'TABLE_UNSUPPORTED',
                        'CATALOG_RESPONSE_INVALID', 'CATALOG_ANCHOR_INVALID',
                        'CATALOG_SCOPE_CONFLICT', 'CATALOG_LINEAGE_INVALID',
                        'CATALOG_LIMIT', 'CATALOG_CHANGED', 'CATALOG_PUBLIC_INVALID'})
_GENERATED = re.compile(re.escape(K.SCHEMA) + r'\.dataops_[0-9a-f]{32}_(?:stg|mart)\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')


class ContractError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else 'CATALOG_RESPONSE_INVALID'
        super().__init__(self.code)


def require(condition, code='CATALOG_RESPONSE_INVALID'):
    if not condition:
        raise ContractError(code)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError):
        raise ContractError('CATALOG_RESPONSE_INVALID') from None


def _bounded(value, limit, code='CATALOG_LIMIT'):
    try:
        size = len(canonical(value).encode('utf-8'))
    except UnicodeError:
        raise ContractError(code) from None
    require(size <= limit, code)


def digest(value):
    try:
        return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
    except UnicodeError:
        raise ContractError('CATALOG_RESPONSE_INVALID') from None


def normalize_request(value):
    require(type(value) is dict and set(value) == {'command', 'table'}, 'REQUEST_INVALID')
    require(type(value['command']) is str and value['command'] == 'describe', 'REQUEST_INVALID')
    require(type(value['table']) is str and value['table'] == TABLE, 'TABLE_UNSUPPORTED')
    return deepcopy(value)


def parse_request(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_INPUT_BYTES, 'INPUT_INVALID')

    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'INPUT_INVALID')
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('INPUT_INVALID')))
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError('INPUT_INVALID') from None
    return normalize_request(value)


def _call(function, *args, code='CATALOG_ANCHOR_INVALID', **kwargs):
    try:
        return function(*args, **kwargs)
    except (K.CurateError, KeyError, TypeError, AttributeError, OverflowError):
        raise ContractError(code) from None


def _text(value, limit=MAX_DESCRIPTION_BYTES):
    if value is None:
        return None
    require(type(value) is str)
    try:
        require(len(value.encode('utf-8')) <= limit, 'CATALOG_LIMIT')
    except UnicodeError:
        raise ContractError('CATALOG_RESPONSE_INVALID') from None
    require(all(ord(char) >= 32 or char in '\n\t' for char in value))
    require('\x7f' not in value)
    return value


def _anchor(value, name):
    require(type(value) is dict, 'CATALOG_ANCHOR_INVALID')
    identity = _call(K._identity, value, name)
    revision = _call(K._revision, value)
    require(revision['updated_at'] <= MAX_INT, 'CATALOG_ANCHOR_INVALID')
    return {'id': identity, 'fully_qualified_name': name,
            'version': str(revision['version']), 'updated_at_ms': revision['updated_at']}


def _node(value, *, root=False):
    require(type(value) is dict, 'CATALOG_LINEAGE_INVALID')
    name = value.get('fullyQualifiedName')
    require(type(name) is str and (name == K.SOURCE if root else _GENERATED.fullmatch(name)),
            'CATALOG_SCOPE_CONFLICT')
    identity = _call(K._identity, value, name, code='CATALOG_LINEAGE_INVALID')
    require(value.get('type') == 'table', 'CATALOG_LINEAGE_INVALID')
    return {'id': identity, 'fully_qualified_name': name, 'sql_presence': 'not-checked'}


def _lineage(value, table_id):
    require(type(value) is dict and {'entity', 'nodes', 'upstreamEdges', 'downstreamEdges'} <= set(value),
            'CATALOG_LINEAGE_INVALID')
    root = _node(value['entity'], root=True)
    require(root['id'] == table_id, 'CATALOG_LINEAGE_INVALID')
    require(type(value['nodes']) is list and len(value['nodes']) <= MAX_NODES, 'CATALOG_LINEAGE_INVALID')
    nodes = [_node(node) for node in value['nodes']]
    identities, names = {table_id}, {K.SOURCE}
    for node in nodes:
        require(node['id'] not in identities and node['fully_qualified_name'] not in names,
                'CATALOG_LINEAGE_INVALID')
        identities.add(node['id'])
        names.add(node['fully_qualified_name'])
    edges, seen, connected = [], set(), set()
    for direction in ('upstreamEdges', 'downstreamEdges'):
        require(type(value[direction]) is list, 'CATALOG_LINEAGE_INVALID')
        require(len(value[direction]) <= MAX_EDGES, 'CATALOG_LIMIT')
        for edge in value[direction]:
            require(type(edge) is dict, 'CATALOG_LINEAGE_INVALID')
            left, right = edge.get('fromEntity'), edge.get('toEntity')
            require(type(left) is str and type(right) is str and left in identities and right in identities
                    and left != right and (left, right) not in seen, 'CATALOG_LINEAGE_INVALID')
            require((right if direction == 'upstreamEdges' else left) == table_id, 'CATALOG_LINEAGE_INVALID')
            details = edge.get('lineageDetails')
            require(details is None or type(details) is dict, 'CATALOG_LINEAGE_INVALID')
            source = None if details is None else details.get('source')
            require(source is None or type(source) is str and source in {'Manual', 'OpenLineage'},
                    'CATALOG_LINEAGE_INVALID')
            edges.append({'from_entity': left, 'to_entity': right, 'source': source})
            seen.add((left, right))
            connected.update((left, right))
    require(len(edges) <= MAX_EDGES, 'CATALOG_LIMIT')
    require({node['id'] for node in nodes} <= connected, 'CATALOG_LINEAGE_INVALID')
    return {'scope': 'one-hop', 'observed': True,
            'nodes': sorted(nodes, key=lambda node: (node['fully_qualified_name'], node['id'])),
            'edges': sorted(edges, key=lambda edge: (edge['from_entity'], edge['to_entity']))}


def project_snapshot(raw):
    require(type(raw) is dict and set(raw) == RAW_KEYS)
    for value in raw.values():
        _bounded(value, MAX_RESPONSE_BYTES)
    anchors = {key: _anchor(raw[key], name) for key, name in ANCHOR_NAMES.items()}
    require(len({value['id'] for value in anchors.values()}) == len(anchors), 'CATALOG_ANCHOR_INVALID')
    require(raw['team'].get('teamType') == 'Group' and raw['service'].get('serviceType') == 'Trino',
            'CATALOG_ANCHOR_INVALID')
    for child, field, parent, kind in [('database', 'service', 'service', 'databaseService'),
                                      ('schema', 'database', 'database', 'database'),
                                      ('table', 'databaseSchema', 'schema', 'databaseSchema')]:
        _call(K._ref, raw[child].get(field), anchors[parent]['id'], kind, ANCHOR_NAMES[parent])
    _call(K._owners, raw['table'], anchors['team']['id'])
    _call(K._ref, raw['table']['owners'][0], anchors['team']['id'], 'team', K.TEAM)
    _call(K._columns, raw['table'], [{'name': 'id', 'type': 'BIGINT'}, {'name': 'name', 'type': 'VARCHAR'}])
    table = raw['table']
    require(all('children' not in column or type(column['children']) is list and not column['children']
                for column in table['columns']), 'CATALOG_ANCHOR_INVALID')
    public = {'schema_version': 1, 'table': QUALIFIED_TABLE,
              'catalog_entity': deepcopy(anchors['table']), 'description': _text(table.get('description')),
              'owners': [{'id': anchors['team']['id'], 'name': K.TEAM, 'type': 'team'}],
              'columns': [{'name': column['name'], 'type': column['dataType'],
                           'comment': _text(column.get('description'))} for column in table['columns']],
              'lineage': _lineage(raw['lineage'], anchors['table']['id']),
              'sql_presence': 'not-checked',
              'observation': {'scope': 'catalog-metadata-only', 'stable': True}}
    _bounded(public, MAX_PUBLIC_BYTES)
    return validate_snapshot({'schema_version': 1, 'anchors': anchors,
                              'response_sha256': {key: digest(raw[key]) for key in sorted(RAW_KEYS)}, 'public': public})


def _validate_anchor(value, name):
    require(type(value) is dict and set(value) == {'id', 'fully_qualified_name', 'version', 'updated_at_ms'},
            'CATALOG_PUBLIC_INVALID')
    require(value['fully_qualified_name'] == name, 'CATALOG_PUBLIC_INVALID')
    _call(K._uuid, value['id'], code='CATALOG_PUBLIC_INVALID')
    version = value['version']
    require(type(version) is str and len(version) <= 32, 'CATALOG_PUBLIC_INVALID')
    try:
        parsed = json.loads(version)
    except ValueError:
        raise ContractError('CATALOG_PUBLIC_INVALID') from None
    require(type(parsed) in (int, float) and str(parsed) == version, 'CATALOG_PUBLIC_INVALID')
    _call(K._revision, {'version': parsed, 'updatedAt': value['updated_at_ms']}, code='CATALOG_PUBLIC_INVALID')
    require(value['updated_at_ms'] <= MAX_INT, 'CATALOG_PUBLIC_INVALID')


def validate_public(value, request):
    normalize_request(request)
    require(type(value) is dict and set(value) == PUBLIC_KEYS, 'CATALOG_PUBLIC_INVALID')
    _bounded(value, MAX_PUBLIC_BYTES)
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['table'] == QUALIFIED_TABLE and value['sql_presence'] == 'not-checked', 'CATALOG_PUBLIC_INVALID')
    observation = value['observation']
    require(type(observation) is dict and set(observation) == {'scope', 'stable'}
            and observation['scope'] == 'catalog-metadata-only' and observation['stable'] is True,
            'CATALOG_PUBLIC_INVALID')
    _validate_anchor(value['catalog_entity'], K.SOURCE)
    _text(value['description'])
    owners = value['owners']
    require(type(owners) is list and len(owners) == 1 and type(owners[0]) is dict
            and set(owners[0]) == {'id', 'name', 'type'} and owners[0]['name'] == K.TEAM
            and owners[0]['type'] == 'team', 'CATALOG_PUBLIC_INVALID')
    _call(K._uuid, owners[0]['id'], code='CATALOG_PUBLIC_INVALID')
    require(owners[0]['id'] != value['catalog_entity']['id'], 'CATALOG_PUBLIC_INVALID')
    require(type(value['columns']) is list and len(value['columns']) == 2, 'CATALOG_PUBLIC_INVALID')
    for column, expected in zip(value['columns'], [('id', 'BIGINT'), ('name', 'VARCHAR')]):
        require(type(column) is dict and set(column) == {'name', 'type', 'comment'}
                and (column['name'], column['type']) == expected, 'CATALOG_PUBLIC_INVALID')
        _text(column['comment'])
    graph = value['lineage']
    require(type(graph) is dict and set(graph) == {'scope', 'observed', 'nodes', 'edges'}
            and graph['scope'] == 'one-hop' and graph['observed'] is True
            and type(graph['nodes']) is list and type(graph['edges']) is list, 'CATALOG_PUBLIC_INVALID')
    raw = {'entity': {'id': value['catalog_entity']['id'], 'fullyQualifiedName': K.SOURCE,
                      'name': 'fixture_v1', 'type': 'table'}, 'nodes': [], 'upstreamEdges': [], 'downstreamEdges': []}
    for node in graph['nodes']:
        require(type(node) is dict and set(node) == {'id', 'fully_qualified_name', 'sql_presence'}
                and type(node['fully_qualified_name']) is str and node['sql_presence'] == 'not-checked', 'CATALOG_PUBLIC_INVALID')
        raw['nodes'].append({'id': node['id'], 'fullyQualifiedName': node['fully_qualified_name'],
                             'name': node['fully_qualified_name'].rsplit('.', 1)[-1], 'type': 'table'})
    for edge in graph['edges']:
        require(type(edge) is dict and set(edge) == {'from_entity', 'to_entity', 'source'}, 'CATALOG_PUBLIC_INVALID')
        direction = 'upstreamEdges' if edge['to_entity'] == value['catalog_entity']['id'] else 'downstreamEdges'
        raw[direction].append({'fromEntity': edge['from_entity'], 'toEntity': edge['to_entity'],
                               'lineageDetails': {'source': edge['source']}})
    require(_lineage(raw, value['catalog_entity']['id']) == graph, 'CATALOG_PUBLIC_INVALID')
    return deepcopy(value)


def validate_snapshot(value):
    require(type(value) is dict and set(value) == {'schema_version', 'anchors', 'response_sha256', 'public'})
    require(type(value['schema_version']) is int and value['schema_version'] == 1)
    require(type(value['anchors']) is dict and set(value['anchors']) == set(ANCHOR_NAMES))
    for key, name in ANCHOR_NAMES.items():
        _validate_anchor(value['anchors'][key], name)
    identities = {anchor['id'] for anchor in value['anchors'].values()}
    require(len(identities) == 5, 'CATALOG_ANCHOR_INVALID')
    hashes = value['response_sha256']
    require(type(hashes) is dict and set(hashes) == RAW_KEYS
            and all(type(sha) is str and _HASH.fullmatch(sha) for sha in hashes.values()))
    public = validate_public(value['public'], {'command': 'describe', 'table': TABLE})
    require(public['catalog_entity'] == value['anchors']['table']
            and public['owners'][0]['id'] == value['anchors']['team']['id'], 'CATALOG_ANCHOR_INVALID')
    require(not ({node['id'] for node in public['lineage']['nodes']} & identities), 'CATALOG_LINEAGE_INVALID')
    return deepcopy(value)


def verify_snapshots(before, after):
    validate_snapshot(before)
    validate_snapshot(after)
    require(canonical(before) == canonical(after), 'CATALOG_CHANGED')
    return True


def project_description(before, after, request):
    normalize_request(request)
    verify_snapshots(before, after)
    return validate_public(after['public'], request)


def receipt_projection(value):
    return validate_public(value, {'command': 'describe', 'table': TABLE})
