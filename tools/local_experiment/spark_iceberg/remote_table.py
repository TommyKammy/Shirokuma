"""Host-owned table boundary for the fixed native Spark remote write experiment.

Only the fresh run name is accepted. A completed host CREATE and captured UUID
are required before Spark starts or a later DROP can be sent. REST responses
are projected before journaling so catalog credentials cannot enter evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import time
import uuid

from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.iceberg_refs import run as REFS
from tools.local_experiment.spark_iceberg import contract as LOCAL
from tools.local_experiment.trino_opa.common import require, token as validate_token, digest


def names(token):
    validate_token(token)
    name = 'spark_' + token
    return {'name': name, 'table': 'iceberg.local_experiment.' + name,
            'path': '/api/catalog/v1/shirokuma_l1/namespaces/local_experiment/tables/' + name,
            'location': 's3://shirokuma-lakehouse/l1/local_experiment/' + name,
            'marker': 'shirokuma-local-spark-iceberg:' + token}


def project(response, token, expected_uuid=None):
    """Validate one known schema and owned location, retaining no REST config."""
    scope = names(token)
    require(type(response) is dict and type(response.get('metadata')) is dict, 'TABLE_METADATA_INVALID')
    value = response['metadata']
    identity = value.get('table-uuid')
    require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'TABLE_UUID_INVALID')
    require(expected_uuid is None or identity == expected_uuid, 'TABLE_UUID_CHANGED')
    require(value.get('format-version') == 2 and type(value.get('format-version')) is int,
            'TABLE_FORMAT_INVALID')
    require(value.get('location') == scope['location'], 'TABLE_LOCATION_CHANGED')
    props = value.get('properties')
    require(type(props) is dict and props.get('comment') == scope['marker'], 'TABLE_OWNER_CHANGED')
    schemas = value.get('schemas')
    require(type(schemas) is list and len(schemas) == 1, 'TABLE_SCHEMA_CHANGED')
    schema = schemas[0]
    require(schema.get('type') == 'struct' and schema.get('schema-id') == value.get('current-schema-id'),
            'TABLE_SCHEMA_CHANGED')
    fields = schema.get('fields')
    require(type(fields) is list and len(fields) == 4, 'TABLE_SCHEMA_CHANGED')
    ids = []
    for field, name, kind in zip(fields, ['id', 'name', 'bucket', 'amount'], ['int', 'string', 'int', 'long']):
        require(type(field) is dict and field.get('name') == name and field.get('type') == kind and
                field.get('required') is False and type(field.get('id')) is int and field['id'] > 0,
                'TABLE_SCHEMA_CHANGED')
        ids.append(field['id'])
    require(len(set(ids)) == 4, 'TABLE_SCHEMA_CHANGED')
    specs = value.get('partition-specs')
    require(type(specs) is list and len(specs) == 1 and specs[0].get('fields') == [] and
            specs[0].get('spec-id') == value.get('default-spec-id'), 'TABLE_PARTITION_CHANGED')
    location = response.get('metadata-location')
    require(type(location) is str and location.startswith(scope['location'] + '/metadata/') and
            location.endswith('.metadata.json') and len(location) < 2048 and
            re.fullmatch(r'[A-Za-z0-9_./:\-]+', location) and
            not any(part in ('.', '..') for part in location.split('/')[3:]), 'METADATA_LOCATION_INVALID')
    snapshots = value.get('snapshots', [])
    require(type(snapshots) is list and len(snapshots) <= 2, 'UNEXPECTED_SNAPSHOT_COUNT')
    current = value.get('current-snapshot-id')
    refs = value.get('refs', {})
    projected = []
    if not snapshots:
        require(current in (None, -1) and refs == {}, 'EMPTY_TABLE_SNAPSHOT_INVALID')
        current = None
    else:
        known = set()
        for row in snapshots:
            sid, parent = row.get('snapshot-id'), row.get('parent-snapshot-id')
            summary = row.get('summary', {})
            require(type(sid) is int and 0 < sid < 2**63 and sid not in known and
                    (parent is None or type(parent) is int and 0 < parent < 2**63) and
                    summary.get('operation') == 'append', 'TABLE_SNAPSHOT_INVALID')
            records, files = summary.get('total-records'), summary.get('total-data-files')
            require(records in ('0', '32') and type(files) is str and re.fullmatch('[0-9]{1,2}', files) and
                    (int(files) == 0 if records == '0' else 1 <= int(files) <= 32), 'SNAPSHOT_COUNTS_INVALID')
            known.add(sid)
            projected.append({'snapshot_id': sid, 'parent_snapshot_id': parent, 'operation': 'append',
                              'record_count': int(records), 'file_count': int(files), 'sha256': digest(row)})
        roots = [s for s in projected if s['parent_snapshot_id'] is None]
        require(len(roots) == 1 and type(current) is int and current in known, 'TABLE_SNAPSHOT_INVALID')
        if len(projected) == 1:
            require(current == roots[0]['snapshot_id'], 'TABLE_SNAPSHOT_INVALID')
        else:
            children = [s for s in projected if s['parent_snapshot_id'] == roots[0]['snapshot_id']]
            require(roots[0]['record_count'] == 0 and len(children) == 1 and
                    children[0]['snapshot_id'] == current and children[0]['record_count'] == 32,
                    'TABLE_SNAPSHOT_CHAIN_INVALID')
        require(refs == {'main': {'type': 'branch', 'snapshot-id': current}} and
                type(refs['main']['snapshot-id']) is int, 'TABLE_REFS_INVALID')
    latest = next((s for s in projected if s['snapshot_id'] == current), None)
    return {'table_uuid': identity, 'table_location': scope['location'], 'marker': scope['marker'],
            'metadata_location': location, 'metadata_sha256': digest(value),
            'schema_sha256': digest(schemas), 'snapshot_id': current, 'snapshot_count': len(snapshots),
            'parent_snapshot_id': latest['parent_snapshot_id'] if latest else None,
            'record_count': latest['record_count'] if latest else 0, 'file_count': latest['file_count'] if latest else 0,
            'snapshots': sorted(projected, key=lambda s: s['snapshot_id'])}


def rows(result, columns, expected):
    require(type(result) is dict and result.get('columns') == columns and result.get('rows') == expected,
            'TRINO_ORACLE_MISMATCH')
    # bool equals 0/1 in Python; insist on the expected scalar types too.
    require(all(type(a) is type(b) for actual, oracle in zip(result['rows'], expected)
                for a, b in zip(actual, oracle)), 'TRINO_ORACLE_TYPE_MISMATCH')
    return deepcopy(result['rows'])


class HostTable:
    def __init__(self, token, worker, control, admin, progress=lambda: None):
        self.token, self.scope = token, names(token)
        self.worker, self.control, self.admin = worker, control, admin
        require(worker.user == SB.run_user(token) and control.user == SB.run_user(token, True),
                'QUERY_IDENTITIES_DIFFER')
        self.progress = progress
        self.deadline = time.monotonic() + 600
        self.journal = {'created': False, 'create_attempted': False, 'drop_attempted': False,
                        'identity': None, 'pending': None, 'steps': [], 'cleanup': {'verified': False}}

    def receipt(self):
        return deepcopy(self.journal)

    def sql(self, label, sql, control=False):
        self.journal['pending'] = label
        self.progress()
        result = SB.bounded_query(self.control if control else self.worker, sql, self.deadline)
        self.journal['steps'].append({'label': label, 'query_id': result['query_id'],
                                      'sql_sha256': hashlib.sha256(sql.encode()).hexdigest()})
        self.journal['pending'] = None
        self.progress()
        return result

    def load(self):
        identity = self.journal['identity']
        result = project(self.admin.request('GET', self.scope['path']), self.token,
                         identity['table_uuid'] if identity else None)
        if identity is not None:
            require(result['schema_sha256'] == identity['schema_sha256'], 'TABLE_SCHEMA_CHANGED')
        return result

    def guard(self):
        require(self.journal['created'] and self.journal['identity'] is not None, 'TABLE_CREATE_UNCONFIRMED')
        return self.load()

    def create(self):
        require(not self.journal['create_attempted'], 'TABLE_CREATE_RETRY_FORBIDDEN')
        existing = self.sql('absence', 'SHOW TABLES FROM iceberg.local_experiment')['rows']
        require([self.scope['name']] not in existing, 'TABLE_ALREADY_EXISTS')
        self.journal['create_attempted'] = True
        self.progress()
        self.sql('create', f"CREATE TABLE {self.scope['table']} (id INTEGER, name VARCHAR, bucket INTEGER, amount BIGINT) "
                 f"COMMENT '{self.scope['marker']}' WITH (format = 'PARQUET', format_version = 2, location = '{self.scope['location']}')")
        identity = self.load()
        self.journal.update(created=True, identity=identity)
        self.progress()
        require(identity['snapshot_count'] <= 1 and identity['record_count'] == identity['file_count'] == 0,
                'TABLE_NOT_EMPTY')
        rows(self.sql('empty', f"SELECT id, name, bucket, amount FROM {self.scope['table']} ORDER BY id"),
             ['id', 'name', 'bucket', 'amount'], [])
        rows(self.sql('empty_files', f'SELECT file_path FROM iceberg.local_experiment."{self.scope["name"]}$files"'),
             ['file_path'], [])
        return deepcopy(identity)

    def verify_written(self):
        identity = self.guard()
        before = self.journal['identity']
        require(identity['snapshot_id'] is not None and identity['snapshot_id'] != before['snapshot_id'] and
                identity['parent_snapshot_id'] == before['snapshot_id'] and identity['record_count'] == 32 and
                identity['snapshot_count'] == before['snapshot_count'] + 1 and
                all(row in identity['snapshots'] for row in before['snapshots']), 'SPARK_SNAPSHOT_MISSING')
        data = rows(self.sql('trino_rows', f"SELECT id, name, bucket, amount FROM {self.scope['table']} ORDER BY id"),
                    ['id', 'name', 'bucket', 'amount'], LOCAL.oracle_rows())
        groups = rows(self.sql('trino_groups', f"SELECT bucket, count(*) AS n, sum(amount) AS total "
                              f"FROM {self.scope['table']} GROUP BY bucket ORDER BY bucket"),
                      ['bucket', 'n', 'total'], LOCAL.oracle_groups())
        snapshots = rows(self.sql('trino_snapshots', f'SELECT snapshot_id, parent_id, operation FROM '
                         f'iceberg.local_experiment."{self.scope["name"]}$snapshots" ORDER BY snapshot_id'),
                         ['snapshot_id', 'parent_id', 'operation'],
                         [[s['snapshot_id'], s['parent_snapshot_id'], s['operation']] for s in identity['snapshots']])
        result = self.sql('trino_files', f'SELECT file_path, record_count, file_size_in_bytes FROM '
                          f'iceberg.local_experiment."{self.scope["name"]}$files" ORDER BY file_path')
        require(result['columns'] == ['file_path', 'record_count', 'file_size_in_bytes'] and
                1 <= len(result['rows']) <= 32, 'DATA_FILES_INVALID')
        files = []
        for path, count, size in result['rows']:
            require(type(path) is str and path.startswith(self.scope['location'] + '/data/') and
                    path.endswith('.parquet') and type(count) is int and 0 < count <= 32 and
                    type(size) is int and 0 < size < 16 * 1024**2, 'DATA_FILE_INVALID')
            files.append({'file_path': path, 'record_count': count, 'file_size_in_bytes': size})
        require(sum(row['record_count'] for row in files) == 32 and
                len({row['file_path'] for row in files}) == len(files), 'DATA_FILES_ORACLE_MISMATCH')
        value = {'identity': identity, 'rows': data, 'groups': groups, 'snapshots': snapshots, 'data_files': files}
        self.journal['verification'] = value
        self.progress()
        return deepcopy(value)

    def cleanup(self, *, writers_stopped, queries_drained):
        require(writers_stopped and queries_drained, 'WRITERS_OR_QUERIES_NOT_DRAINED')
        self.deadline = time.monotonic() + 90
        if not self.journal['create_attempted']:
            self.journal['cleanup'] = {'verified': True, 'action': 'not-created'}
        else:
            self.guard()
            require(not self.journal['drop_attempted'], 'TABLE_DROP_RETRY_FORBIDDEN')
            self.journal['drop_attempted'] = True
            self.progress()
            self.sql('drop', 'DROP TABLE ' + self.scope['table'], control=True)
            remaining = self.sql('absence_after_drop', 'SHOW TABLES FROM iceberg.local_experiment', control=True)['rows']
            require([self.scope['name']] not in remaining, 'TABLE_CLEANUP_UNVERIFIED')
            self.journal['cleanup'] = {'verified': True, 'action': 'same-uuid-drop',
                                       'table_uuid': self.journal['identity']['table_uuid']}
        self.progress()
        return deepcopy(self.journal['cleanup'])


def fixture_snapshot(client):
    result = {}
    for key, sql in REFS.FIXTURE_SQL.items():
        value = client.query(sql)
        result[key] = {'columns': value['columns'], 'column_types': value['column_types'], 'rows': value['rows']}
    require(result['rows']['rows'] == SB.ROWS_A, 'FIXTURE_ROWS_DIFFER')
    return result
