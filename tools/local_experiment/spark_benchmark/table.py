"""Own one CTAS table and bind both engines to its verified immutable snapshot."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import uuid

from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.spark_iceberg.remote_table import fixture_snapshot
from tools.local_experiment.spark_benchmark import workload as W
from tools.local_experiment.trino_opa.common import digest, token as validate_token


class TableError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise TableError(code)


def names(token):
    validate_token(token)
    name = 'spark_' + token
    return {'name': name, 'table': 'iceberg.local_experiment.' + name,
            'path': '/api/catalog/v1/shirokuma_l1/namespaces/local_experiment/tables/' + name,
            'location': 's3://shirokuma-lakehouse/l1/local_experiment/' + name,
            'marker': 'shirokuma-local-spark-benchmark:' + token}


def sid(value):
    require(type(value) is int and 0 < value < 2**63, 'SNAPSHOT_ID_INVALID')
    return value


def owned_path(value, prefix, suffix):
    require(type(value) is str and len(value) <= 2048 and value.startswith(prefix)
            and value.endswith(suffix) and re.fullmatch(r'[A-Za-z0-9_./:\-]+', value)
            and not any(p in ('.', '..') for p in value.split('/')[3:]), 'TABLE_PATH_INVALID')
    return value


def project(response, token, expected_uuid=None):
    scope = names(token)
    require(type(response) is dict and type(response.get('metadata')) is dict, 'TABLE_METADATA_INVALID')
    value = response['metadata']
    identity = value.get('table-uuid')
    require(type(identity) is str and re.fullmatch(r'[0-9a-f-]{36}', identity), 'TABLE_UUID_INVALID')
    try:
        require(str(uuid.UUID(identity)) == identity, 'TABLE_UUID_INVALID')
    except ValueError:
        raise TableError('TABLE_UUID_INVALID') from None
    require(expected_uuid is None or identity == expected_uuid, 'TABLE_UUID_CHANGED')
    require(type(value.get('format-version')) is int and value['format-version'] == 2, 'TABLE_FORMAT_INVALID')
    require(value.get('location') == scope['location'] and type(value.get('properties')) is dict
            and value['properties'].get('comment') == scope['marker'], 'TABLE_OWNER_OR_LOCATION_CHANGED')
    schemas = value.get('schemas')
    require(type(schemas) is list and len(schemas) == 1 and type(schemas[0]) is dict, 'TABLE_SCHEMA_CHANGED')
    schema = schemas[0]
    require(schema.get('type') == 'struct' and type(schema.get('schema-id')) is int
            and schema['schema-id'] == value.get('current-schema-id'), 'TABLE_SCHEMA_CHANGED')
    fields = schema.get('fields')
    require(type(fields) is list and len(fields) == 9, 'TABLE_SCHEMA_CHANGED')
    expected_types = ['long', 'int', 'string', 'string', 'decimal(12,2)', 'decimal(15,2)',
                      'decimal(3,2)', 'decimal(3,2)', 'date']
    ids = []
    for field, name, kind in zip(fields, W.SOURCE_COLUMNS, expected_types):
        require(type(field) is dict and field.get('name') == name and type(field.get('type')) is str
                and field['type'].replace(' ', '') == kind and field.get('required') is False
                and type(field.get('id')) is int and field['id'] > 0, 'TABLE_SCHEMA_CHANGED')
        ids.append(field['id'])
    require(len(set(ids)) == 9, 'TABLE_SCHEMA_CHANGED')
    specs = value.get('partition-specs')
    require(type(specs) is list and len(specs) == 1 and type(specs[0]) is dict
            and specs[0].get('fields') == [] and type(specs[0].get('spec-id')) is int
            and specs[0]['spec-id'] == value.get('default-spec-id'), 'TABLE_PARTITION_CHANGED')
    metadata = owned_path(response.get('metadata-location'), scope['location'] + '/metadata/', '.metadata.json')
    snapshots = value.get('snapshots')
    require(type(snapshots) is list and 1 <= len(snapshots) <= 2, 'TABLE_SNAPSHOTS_INVALID')
    projected, known = [], set()
    for item in snapshots:
        require(type(item) is dict and type(item.get('summary')) is dict, 'TABLE_SNAPSHOTS_INVALID')
        snapshot, parent = sid(item.get('snapshot-id')), item.get('parent-snapshot-id')
        if parent is not None:
            sid(parent)
        summary = item['summary']
        require(snapshot not in known and summary.get('operation') == 'append', 'TABLE_SNAPSHOTS_INVALID')
        counts = []
        for key, limit in [('total-records', W.MAX_SOURCE_ROWS), ('total-data-files', 32)]:
            count = summary.get(key)
            require(type(count) is str and re.fullmatch(r'0|[1-9][0-9]{0,9}', count)
                    and int(count) <= limit, 'TABLE_SNAPSHOT_COUNTS_INVALID')
            counts.append(int(count))
        require((counts[0] == 0) == (counts[1] == 0), 'TABLE_SNAPSHOT_COUNTS_INVALID')
        projected.append({'snapshot_id': snapshot, 'parent_snapshot_id': parent,
                          'record_count': counts[0], 'file_count': counts[1]})
        known.add(snapshot)
    current = sid(value.get('current-snapshot-id'))
    roots = [s for s in projected if s['parent_snapshot_id'] is None]
    require(len(roots) == 1 and current in known, 'TABLE_SNAPSHOT_CHAIN_INVALID')
    latest = next(s for s in projected if s['snapshot_id'] == current)
    require(latest['record_count'] > 0 and latest['file_count'] > 0, 'TABLE_NOT_POPULATED')
    if len(projected) == 1:
        require(latest == roots[0], 'TABLE_SNAPSHOT_CHAIN_INVALID')
    else:
        require(latest['parent_snapshot_id'] == roots[0]['snapshot_id'] and roots[0]['record_count'] == 0,
                'TABLE_SNAPSHOT_CHAIN_INVALID')
    require(value.get('refs') == {'main': {'type': 'branch', 'snapshot-id': current}}
            and type(value['refs']['main']['snapshot-id']) is int, 'TABLE_REFS_CHANGED')
    return {'identity': {'table_uuid': identity, 'table_location': scope['location'], 'marker': scope['marker'],
                         'snapshot_id': current, 'parent_snapshot_id': latest['parent_snapshot_id'],
                         'metadata_location': metadata, 'operation': 'append'},
            'metadata_sha256': digest(value), 'schema_sha256': digest(schemas),
            'record_count': latest['record_count'], 'file_count': latest['file_count'], 'snapshots': projected}


def files_projection(result, token):
    scope = names(token)
    require(result['columns'] == ['file_path', 'record_count', 'file_size_in_bytes']
            and result['column_types'] == ['varchar', 'bigint', 'bigint']
            and type(result['rows']) is list and 1 <= len(result['rows']) <= 32, 'DATA_FILES_INVALID')
    files = []
    for row in result['rows']:
        require(type(row) is list and len(row) == 3, 'DATA_FILES_INVALID')
        path, count, size = row
        owned_path(path, scope['location'] + '/data/', '.parquet')
        require(type(count) is int and 0 < count <= W.MAX_SOURCE_ROWS and type(size) is int
                and 0 < size <= W.MAX_SOURCE_BYTES, 'DATA_FILES_INVALID')
        files.append({'path': path, 'record_count': count, 'file_size_in_bytes': size})
    require(len({f['path'] for f in files}) == len(files) and
            sum(f['record_count'] for f in files) <= W.MAX_SOURCE_ROWS and
            sum(f['file_size_in_bytes'] for f in files) <= W.MAX_SOURCE_BYTES, 'DATA_FILES_BOUND')
    return sorted(files, key=lambda f: f['path'])


class HostTable:
    def __init__(self, token, worker, control, admin, progress=lambda: None):
        self.token, self.scope = token, names(token)
        require(worker.user == SB.run_user(token) and control.user == SB.run_user(token, True),
                'QUERY_IDENTITIES_DIFFER')
        self.worker, self.control, self.admin, self.progress = worker, control, admin, progress
        self.journal = {'schema_version': 1, 'run_token': token, 'created': False,
                        'prepare_attempted': False, 'create_attempted': False, 'drop_attempted': False,
                        'identity': None, 'sealed': None, 'pending': None, 'steps': [],
                        'measurement_attempted': False, 'measurements': [], 'cleanup': {'verified': False}}

    def receipt(self):
        return deepcopy(self.journal)

    def step(self, label, sql, *, consume=None, control=False):
        client = self.control if control else self.worker
        entry = {'label': label, 'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
                 'query_id': None, 'result': 'pending'}
        self.journal['steps'].append(entry)
        self.journal['pending'] = label
        try:
            self.progress()
            if consume is None:
                result = client.query(sql)
            else:
                result = client.stream(sql, consume, expected_columns=W.SOURCE_COLUMNS,
                                       expected_types=W.SOURCE_COLUMN_TYPES)
            entry.update(query_id=result['query_id'], result='passed')
            self.journal['pending'] = None
            self.progress()
            return result
        except BaseException:
            entry['result'] = 'failed-or-unknown'
            try:
                self.progress()
            except BaseException:
                pass
            raise

    def load(self):
        saved = self.journal['identity']
        current = project(self.admin.request('GET', self.scope['path']), self.token,
                          saved['identity']['table_uuid'] if saved else None)
        if saved is not None:
            require(current['schema_sha256'] == saved['schema_sha256'], 'TABLE_SCHEMA_CHANGED')
        return current

    def guard(self):
        require(self.journal['created'] and self.journal['identity'] is not None, 'TABLE_CREATE_UNCONFIRMED')
        return self.load()

    def data_files(self):
        return files_projection(self.step('data_files', 'SELECT file_path, record_count, file_size_in_bytes FROM '
                                f'iceberg.local_experiment."{self.scope["name"]}$files" ORDER BY file_path'), self.token)

    def prepare(self):
        require(not self.journal['prepare_attempted'], 'TABLE_PREPARE_RETRY_FORBIDDEN')
        self.journal['prepare_attempted'] = True
        self.progress()
        present = self.step('absence', 'SHOW TABLES FROM iceberg.local_experiment')['rows']
        require(type(present) is list and all(type(row) is list and len(row) == 1 and type(row[0]) is str for row in present)
                and [self.scope['name']] not in present, 'TABLE_ALREADY_EXISTS_OR_INVALID_INVENTORY')
        columns = self.step('source_schema', 'SHOW COLUMNS FROM tpch."sf0.1".lineitem')
        require(columns['columns'][:2] == ['Column', 'Type'], 'SOURCE_SCHEMA_INVALID')
        raw_schema = {}
        for row in columns['rows']:
            require(type(row) is list and len(row) >= 2 and type(row[0]) is str and type(row[1]) is str
                    and row[0] not in raw_schema, 'SOURCE_SCHEMA_INVALID')
            raw_schema[row[0]] = W.normalize_column_types('trino', [row[1]])[0]
        require(all(raw_schema.get(name) == kind for name, kind in zip(W.SOURCE_COLUMNS, W.RAW_SOURCE_COLUMN_TYPES)),
                'SOURCE_SCHEMA_CHANGED')
        validation = self.step('source_validation', W.source_validation_sql())
        require(validation['columns'] == ['row_count', 'invalid_identity_rows', 'invalid_numeric_rows']
                and validation['column_types'] == ['bigint'] * 3 and len(validation['rows']) == 1
                and type(validation['rows'][0]) is list and len(validation['rows'][0]) == 3
                and all(type(v) is int for v in validation['rows'][0])
                and 1 <= validation['rows'][0][0] <= W.MAX_SOURCE_ROWS and validation['rows'][0][1:] == [0, 0],
                'SOURCE_VALUES_INVALID')
        self.journal['source_validation'] = {'schema': [[name, raw_schema[name]] for name in W.SOURCE_COLUMNS],
                                             'row_count': validation['rows'][0][0],
                                             'invalid_identity_rows': 0, 'invalid_numeric_rows': 0}
        source = W.DatasetOracle()
        self.step('source_rows', W.source_rows_sql(), consume=source.add)
        dataset = source.finish()
        require(dataset['row_count'] == validation['rows'][0][0], 'SOURCE_STREAM_COUNT_CHANGED')
        self.journal['source_dataset'] = deepcopy(dataset)
        self.progress()
        self.journal['create_attempted'] = True
        self.progress()
        self.step('create', f"CREATE TABLE {self.scope['table']} COMMENT '{self.scope['marker']}' "
                  f"WITH (format = 'PARQUET', format_version = 2, location = '{self.scope['location']}') AS "
                  + W.source_select_sql())
        identity = self.load()
        self.journal.update(created=True, identity=identity)
        self.progress()
        require(identity['record_count'] == dataset['row_count'], 'CREATED_ROW_COUNT_DIFFERENT')
        files = self.data_files()
        require(len(files) == identity['file_count'] and sum(f['record_count'] for f in files) == dataset['row_count'],
                'CREATED_FILE_COUNTS_DIFFERENT')
        copied = W.DatasetOracle()
        self.step('table_rows', W.table_rows_sql(self.token, identity['identity']['snapshot_id']), consume=copied.add)
        require(copied.finish() == dataset, 'CTAS_DATASET_ORACLE_MISMATCH')
        require(self.guard() == identity, 'TABLE_CHANGED_DURING_SEAL')
        sealed = {'identity': deepcopy(identity['identity']), 'data_files': files, 'dataset': dataset}
        self.journal['sealed'] = sealed
        self.progress()
        return deepcopy(sealed)

    def guard_seal(self):
        sealed = self.journal['sealed']
        require(sealed is not None, 'TABLE_NOT_SEALED')
        require(self.guard() == self.journal['identity'], 'SEALED_TABLE_CHANGED')
        require(self.data_files() == sealed['data_files'], 'SEALED_DATA_FILES_CHANGED')
        require(self.guard() == self.journal['identity'], 'SEALED_TABLE_CHANGED')
        return deepcopy(sealed)

    def measure(self):
        require(not self.journal['measurement_attempted'], 'MEASUREMENT_RETRY_FORBIDDEN')
        self.journal['measurement_attempted'] = True
        self.progress()
        for case_id in W.CASES:
            for index in W.SAMPLE_INDICES:
                sealed = self.guard_seal()
                sql = W.query_sql('trino', case_id, self.token, sealed['identity']['snapshot_id'])
                result = self.step('measure.' + case_id + '.' + str(index), sql)
                normalized = W.normalize_result(case_id, 'trino', result['columns'], result['column_types'], result['rows'])
                require(normalized == sealed['dataset']['expected'][case_id], 'MEASUREMENT_ORACLE_MISMATCH')
                require(type(result['elapsed_ns']) is int and result['elapsed_ns'] > 0, 'MEASUREMENT_TIME_INVALID')
                sample = {'engine': 'trino', 'case_id': case_id, 'sample_index': index,
                          'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(), 'elapsed_ns': result['elapsed_ns'],
                          'result': normalized, 'query_id': result['query_id'], 'stats': deepcopy(result['stats'])}
                self.journal['measurements'].append(sample)
                self.progress()
                self.guard_seal()
        return deepcopy(self.journal['measurements'])

    def cleanup(self, *, writers_stopped, queries_drained):
        require(writers_stopped and queries_drained, 'WRITERS_OR_QUERIES_NOT_DRAINED')
        if not self.journal['create_attempted']:
            self.journal['cleanup'] = {'verified': True, 'action': 'not-created'}
        else:
            self.guard()
            require(not self.journal['drop_attempted'], 'TABLE_DROP_RETRY_FORBIDDEN')
            self.journal['drop_attempted'] = True
            self.progress()
            self.step('drop', 'DROP TABLE ' + self.scope['table'], control=True)
            remaining = self.step('absence_after_drop', 'SHOW TABLES FROM iceberg.local_experiment', control=True)['rows']
            require(type(remaining) is list and all(type(row) is list and len(row) == 1 and type(row[0]) is str for row in remaining)
                    and [self.scope['name']] not in remaining, 'TABLE_CLEANUP_UNVERIFIED')
            self.journal['cleanup'] = {'verified': True, 'action': 'same-uuid-drop',
                                       'table_uuid': self.journal['identity']['identity']['table_uuid']}
        self.progress()
        return deepcopy(self.journal['cleanup'])
