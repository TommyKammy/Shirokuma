"""Catalog-only display; real HTTP/authentication/runtime are never invoked."""
from copy import deepcopy
import json
import unittest
import uuid

from tools.local_experiment.catalog_read import contract as C


CANARY = 'PRIVATE_CONNECTION_SQL_OR_HREF_CANARY'


def request():
    return {'command': 'describe', 'table': 'smoke.fixture_v1'}


def identity(number):
    return str(uuid.UUID(int=number))


def entity(number, name, **extra):
    return {'id': identity(number), 'name': name.rsplit('.', 1)[-1],
            'fullyQualifiedName': name, 'deleted': False, 'version': 0.1,
            'updatedAt': 1789541704000, 'href': CANARY, **extra}


def reference(value, kind):
    return {key: value[key] for key in ('id', 'name', 'fullyQualifiedName', 'deleted')} | {'type': kind, 'href': CANARY}


def documents(*, empty=False):
    """API fields follow saved ingestion; only safe fixed fixture facts are used."""
    team = entity(1, C.K.TEAM, teamType='Group')
    service = entity(2, C.K.SERVICE, serviceType='Trino', connection={'password': CANARY})
    database = entity(3, C.K.DATABASE, service=reference(service, 'databaseService'))
    schema = entity(4, C.K.SOURCE_SCHEMA, database=reference(database, 'database'))
    table = entity(5, C.K.SOURCE, databaseSchema=reference(schema, 'databaseSchema'),
                   owners=[reference(team, 'team')],
                   description='Retained fixture: schema and rows observed live through Trino.',
                   columns=[{'name': 'id', 'dataType': 'BIGINT', 'dataTypeDisplay': 'bigint',
                             'children': [], 'tags': [], 'fullyQualifiedName': C.K.SOURCE + '.id'},
                            {'name': 'name', 'dataType': 'VARCHAR', 'dataTypeDisplay': 'varchar',
                             'dataLength': 2147483647, 'children': [], 'tags': [],
                             'fullyQualifiedName': C.K.SOURCE + '.name'}])
    generated = entity(6, C.K.SCHEMA + '.dataops_' + 'a' * 32 + '_stg')
    lineage = {'entity': reference(table, 'table'), 'nodes': [] if empty else [reference(generated, 'table')],
               'upstreamEdges': [], 'downstreamEdges': [] if empty else [
                   {'fromEntity': table['id'], 'toEntity': generated['id'],
                    'lineageDetails': {'source': 'OpenLineage', 'pipeline': {'id': identity(20)},
                                      'description': CANARY, 'sqlQuery': CANARY, 'columnsLineage': [],
                                      'createdAt': 1789541704000, 'updatedAt': 1789541704000}}]}
    return dict(team=team, service=service, database=database, schema=schema, table=table, lineage=lineage)


def snapshot(raw=None):
    return C.project_snapshot(documents() if raw is None else raw)


def public(raw=None):
    value = snapshot(raw)
    return C.project_description(value, deepcopy(value), request())


class CatalogReadContractTests(unittest.TestCase):
    def refused(self, function, *args, code=None):
        with self.assertRaises(C.ContractError) as caught:
            function(*args)
        self.assertIn(str(caught.exception), C.ERROR_CODES)
        self.assertNotIn(CANARY, str(caught.exception))
        if code:
            self.assertEqual(str(caught.exception), code)

    def test_request_is_one_fixed_fixture_with_no_arbitrary_fields(self):
        self.assertEqual(C.normalize_request(request()), request())
        for value in [None, [], {}, {**request(), 'command': 'select'}, {**request(), 'table': True},
                      {**request(), 'table': 'iceberg.smoke.fixture_v1'},
                      {**request(), 'table': 'local_experiment.dataops_' + 'a' * 32 + '_stg'},
                      {**request(), 'table': '../fixture_v1'}, {**request(), 'sql': CANARY}]:
            self.refused(C.normalize_request, value)

    def test_json_duplicate_nonfinite_invalid_utf8_and_budget_are_refused(self):
        self.assertEqual(C.parse_request(json.dumps(request()).encode()), request())
        for raw in [b'', b'\xff', b'{}{}', b'null', b'NaN', b'{"command":"describe","table":"smoke.fixture_v1","table":"smoke.fixture_v1"}',
                    b' ' * (C.MAX_INPUT_BYTES + 1), '{"command":"describe","table":"smoke.fixture_v1"}']:
            self.refused(C.parse_request, raw)

    def test_realistic_shapes_project_exact_display_and_no_raw_values(self):
        value = public()
        self.assertEqual(set(value), C.PUBLIC_KEYS)
        self.assertEqual(value['description'], documents()['table']['description'])
        self.assertEqual(value['owners'], [{'id': identity(1), 'name': C.K.TEAM, 'type': 'team'}])
        self.assertEqual(value['columns'], [{'name': 'id', 'type': 'BIGINT', 'comment': None},
                                           {'name': 'name', 'type': 'VARCHAR', 'comment': None}])
        self.assertEqual(value['catalog_entity']['id'], identity(5))
        self.assertEqual(value['catalog_entity']['version'], '0.1')
        self.assertEqual(value['sql_presence'], 'not-checked')
        self.assertEqual(value['lineage']['nodes'][0]['sql_presence'], 'not-checked')
        self.assertEqual(value['lineage']['edges'][0]['source'], 'OpenLineage')
        self.assertNotIn(CANARY, C.canonical(snapshot()))
        self.assertEqual(C.receipt_projection(value), value)
        value['columns'][0]['name'] = 'mutated'
        self.assertEqual(public()['columns'][0]['name'], 'id')

    def test_description_and_comment_preserve_null_empty_unicode_and_newlines(self):
        raw = documents()
        raw['table']['description'] = None
        raw['table']['columns'][0]['description'] = ''
        raw['table']['columns'][1]['description'] = '日本語\n説明\tA'
        value = public(raw)
        self.assertIsNone(value['description'])
        self.assertEqual(value['columns'][0]['comment'], '')
        self.assertEqual(value['columns'][1]['comment'], '日本語\n説明\tA')

    def test_missing_lineage_is_never_observed_empty(self):
        value = public(documents(empty=True))
        self.assertEqual(value['lineage'], {'scope': 'one-hop', 'observed': True, 'nodes': [], 'edges': []})
        for key in ('lineage', 'nodes', 'upstreamEdges', 'downstreamEdges'):
            raw = documents(empty=True)
            if key == 'lineage':
                del raw[key]
            else:
                del raw['lineage'][key]
            self.refused(C.project_snapshot, raw)
        for bad in (None, False, {}, ''):
            raw = documents(empty=True)
            raw['lineage']['downstreamEdges'] = bad
            self.refused(C.project_snapshot, raw)

    def test_anchor_uuid_name_parent_owner_type_and_deleted_guards(self):
        mutations = [lambda d: d['table'].update(deleted=True),
                     lambda d: d['team'].update(deleted=None),
                     lambda d: d['service'].update(serviceType='Mysql'),
                     lambda d: d['team'].update(teamType='Organization'),
                     lambda d: d['table'].update(name='foreign'),
                     lambda d: d['table'].update(id='not-a-uuid'),
                     lambda d: d['table'].update(id=d['schema']['id']),
                     lambda d: d['schema']['database'].update(id=identity(99)),
                     lambda d: d['database']['service'].update(fullyQualifiedName='foreign'),
                     lambda d: d['table']['databaseSchema'].update(type='table'),
                     lambda d: d['table']['owners'][0].update(id=identity(99)),
                     lambda d: d['table']['owners'][0].update(fullyQualifiedName='ForeignTeam'),
                     lambda d: d['table']['owners'].append(deepcopy(d['table']['owners'][0]))]
        for mutate in mutations:
            raw = documents(); mutate(raw)
            self.refused(C.project_snapshot, raw)

    def test_fixture_columns_are_ordered_exact_metadata_types(self):
        for mutate in [lambda d: d['table']['columns'].reverse(),
                       lambda d: d['table']['columns'][0].update(dataType='INTEGER'),
                       lambda d: d['table']['columns'][0].update(name='other'),
                       lambda d: d['table']['columns'][0].update(children=[{'name': 'child'}]),
                       lambda d: d['table']['columns'][0].update(children=False),
                       lambda d: d['table']['columns'].pop()]:
            raw = documents(); mutate(raw)
            self.refused(C.project_snapshot, raw)

    def test_revision_and_text_boundary_do_not_leak_bad_values(self):
        for version in (None, True, 0, -1, float('nan'), float('inf'), '0.1'):
            raw = documents(); raw['table']['version'] = version
            self.refused(C.project_snapshot, raw)
        for timestamp in (True, 0, -1, 1.5, C.MAX_INT + 1):
            raw = documents(); raw['table']['updatedAt'] = timestamp
            self.refused(C.project_snapshot, raw)
        for text in (True, [], 'a' * 2049, '日' * 683, '\ud800', '\x1b[31m', '\x00', '\x7f'):
            raw = documents(); raw['table']['description'] = text
            self.refused(C.project_snapshot, raw)
        raw = documents(); raw['table']['description'] = 'a' * 2048
        self.assertEqual(len(public(raw)['description']), 2048)

    def test_graph_uuid_fqn_deleted_and_foreign_scope_are_refused(self):
        mutations = [lambda g: g['entity'].update(id=identity(99)),
                     lambda g: g['entity'].update(type='pipeline'),
                     lambda g: g['nodes'][0].update(deleted=True),
                     lambda g: g['nodes'][0].update(id=identity(1)),
                     lambda g: g['nodes'][0].update(fullyQualifiedName='foreign.db.table'),
                     lambda g: g['nodes'][0].update(type='pipeline'),
                     lambda g: g['nodes'].append(deepcopy(g['nodes'][0])),
                     lambda g: g['nodes'][0].update(id=identity(5))]
        for mutate in mutations:
            raw = documents(); mutate(raw['lineage'])
            self.refused(C.project_snapshot, raw)

    def test_graph_edges_must_be_one_hop_unique_incident_and_resolved(self):
        mutations = [lambda g: g['downstreamEdges'][0].update(toEntity=identity(99)),
                     lambda g: g['downstreamEdges'][0].update(fromEntity=identity(6)),
                     lambda g: g['downstreamEdges'][0].update(toEntity=identity(5)),
                     lambda g: g['downstreamEdges'].append(deepcopy(g['downstreamEdges'][0])),
                     lambda g: g['upstreamEdges'].append(deepcopy(g['downstreamEdges'][0])),
                     lambda g: g.update(downstreamEdges=[]),
                     lambda g: g['downstreamEdges'][0].update(lineageDetails=False),
                     lambda g: g['downstreamEdges'][0]['lineageDetails'].update(source='UnverifiedSource')]
        for mutate in mutations:
            raw = documents(); mutate(raw['lineage'])
            self.refused(C.project_snapshot, raw)

    def test_unspecified_source_stays_null_and_manual_is_not_openlineage(self):
        raw = documents(); del raw['lineage']['downstreamEdges'][0]['lineageDetails']
        self.assertIsNone(public(raw)['lineage']['edges'][0]['source'])
        raw = documents(); raw['lineage']['downstreamEdges'][0]['lineageDetails']['source'] = 'Manual'
        self.assertEqual(public(raw)['lineage']['edges'][0]['source'], 'Manual')

    def test_raw_response_and_graph_cardinality_are_bounded(self):
        raw = documents(); raw['service']['connection'] = 'a' * C.MAX_RESPONSE_BYTES
        self.refused(C.project_snapshot, raw, code='CATALOG_LIMIT')
        raw = documents(); raw['lineage']['nodes'] *= 65
        self.refused(C.project_snapshot, raw)
        raw = documents(); raw['lineage']['downstreamEdges'] *= 65
        self.refused(C.project_snapshot, raw)

    def test_maximum_legal_text_and_one_hop_graph_fit_public_budget(self):
        raw = documents(empty=True)
        raw['table']['description'] = 'a' * C.MAX_DESCRIPTION_BYTES
        for column in raw['table']['columns']:
            column['description'] = 'b' * C.MAX_DESCRIPTION_BYTES
        for number in range(C.MAX_NODES):
            node = entity(number + 100, C.K.SCHEMA + '.dataops_' + format(number, '032x') + '_stg')
            raw['lineage']['nodes'].append(reference(node, 'table'))
            raw['lineage']['downstreamEdges'].append(
                {'fromEntity': identity(5), 'toEntity': node['id'], 'lineageDetails': {'source': 'Manual'}})
        value = public(raw)
        self.assertEqual(len(value['lineage']['nodes']), 64)
        self.assertEqual(len(value['lineage']['edges']), 64)
        self.assertLessEqual(len(C.canonical(value).encode()), C.MAX_PUBLIC_BYTES)
        self.assertEqual(C.validate_public(value, request()), value)

    def test_two_observations_detect_published_and_unpublished_drift(self):
        before = snapshot()
        self.assertTrue(C.verify_snapshots(before, deepcopy(before)))
        for mutate in [lambda d: d['table'].update(description='new'),
                       lambda d: d['team'].update(version=0.2),
                       lambda d: d['table'].update(href='different-unpublished-href'),
                       lambda d: d['lineage']['downstreamEdges'][0]['lineageDetails'].update(updatedAt=1789541704001)]:
            raw = documents(); mutate(raw)
            after = snapshot(raw)
            self.refused(C.project_description, before, after, request(), code='CATALOG_CHANGED')
        self.refused(C.verify_snapshots, before, snapshot(documents(empty=True)), code='CATALOG_CHANGED')

    def test_snapshot_shape_hashes_and_binding_tampering_are_refused(self):
        for mutate in [lambda s: s.update(schema_version=True), lambda s: s.update(extra='raw'),
                       lambda s: s['response_sha256'].update(table='bad'),
                       lambda s: s['anchors']['table'].update(id=identity(30)),
                       lambda s: s['public']['owners'][0].update(id=identity(30)),
                       lambda s: s['anchors']['team'].update(version='01.0')]:
            value = snapshot(); mutate(value)
            self.refused(C.validate_snapshot, value)

    def test_public_exact_types_privacy_and_not_checked_claims(self):
        mutations = [lambda p: p.update(schema_version=True), lambda p: p.update(rows=[]),
                     lambda p: p.update(sql_presence='present'), lambda p: p.update(table='foreign'),
                     lambda p: p['catalog_entity'].update(href=CANARY),
                     lambda p: p['observation'].update(stable=1),
                     lambda p: p['columns'][0].update(nullable=True),
                     lambda p: p['lineage'].update(observed=1),
                     lambda p: p['lineage']['nodes'][0].update(sql_presence='removed'),
                     lambda p: p['lineage']['edges'][0].update(sqlQuery=CANARY)]
        for mutate in mutations:
            value = public(); mutate(value)
            self.refused(C.validate_public, value, request())

    def test_projection_and_validation_do_not_mutate_caller_values(self):
        raw = documents(); saved = deepcopy(raw)
        before = C.project_snapshot(raw); before_saved = deepcopy(before)
        value = C.project_description(before, deepcopy(before), request())
        self.assertEqual(raw, saved)
        self.assertEqual(before, before_saved)
        value['owners'][0]['id'] = identity(99)
        self.assertEqual(before, before_saved)


if __name__ == '__main__':
    unittest.main()
