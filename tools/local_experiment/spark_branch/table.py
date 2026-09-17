"""One new owned table, two conditional refs, and an independent branch append.

The old remote-write and named-ref contracts remain unchanged. Catalog responses
are reduced to typed identity/snapshot evidence; credentials never enter this
journal. No uncertain mutation is retried or adopted for cleanup.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import time
import uuid

from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.spark_iceberg.remote_access import AdminClient
from tools.local_experiment.spark_iceberg.remote_table import names, fixture_snapshot
from tools.local_experiment.trino_opa.common import digest
from tools.local_experiment.spark_branch import contract as C

REFS = {'baseline_tag': 'tag', 'audit_branch': 'branch'}
MAX_FILES = 8
MAX_FILE_BYTES = 16 * 1024**2
MAX_SNAPSHOT_BYTES = 64 * 1024**2


class TableError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise TableError(code)


def same(left, right):
    return digest(left) == digest(right)


def sid(value):
    require(type(value) is int and 0 < value < 2**63, 'SNAPSHOT_ID_INVALID')
    return value


def owned_path(value, prefix, suffix):
    require(type(value) is str and len(value) <= 2048 and value.startswith(prefix)
            and value.endswith(suffix) and re.fullmatch(r'[A-Za-z0-9_./:\-]+', value)
            and not any(p in ('.', '..') for p in value.split('/')[3:]), 'TABLE_PATH_INVALID')
    return value


def validate_ref_body(body, token):
    """Only absent fixed tag/branch creation, with table UUID and main CAS."""
    names(token)
    require(type(body) is dict and set(body) == {'requirements', 'updates'}, 'REF_BODY_INVALID')
    requirements, updates = body['requirements'], body['updates']
    require(type(requirements) is list and len(requirements) == 3
            and type(updates) is list and len(updates) == 1 and type(updates[0]) is dict,
            'REF_BODY_INVALID')
    item = updates[0]
    require(set(item) == {'action', 'ref-name', 'type', 'snapshot-id'}
            and item['action'] == 'set-snapshot-ref' and type(item['ref-name']) is str
            and item['ref-name'] in REFS and item['type'] == REFS[item['ref-name']], 'REF_BODY_INVALID')
    target = sid(item['snapshot-id'])
    require(type(requirements[0]) is dict and set(requirements[0]) == {'type', 'uuid'}
            and requirements[0]['type'] == 'assert-table-uuid', 'REF_BODY_INVALID')
    identity = requirements[0]['uuid']
    try:
        require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'REF_UUID_INVALID')
    except ValueError:
        raise TableError('REF_UUID_INVALID') from None
    require(same(requirements[1], {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': target})
            and same(requirements[2], {'type': 'assert-ref-snapshot-id', 'ref': item['ref-name'], 'snapshot-id': None}),
            'REF_BODY_INVALID')
    return body


class BranchAdmin(AdminClient):
    """Keep existing management allowlists; add one fixed catalog commit method."""
    def commit_reference(self, body):
        validate_ref_body(body, self.run_token)
        return self._exchange('POST', self.p['table'], body, statuses=(200,))


def project(response, token, expected_uuid=None):
    scope = names(token)
    require(type(response) is dict and type(response.get('metadata')) is dict, 'TABLE_METADATA_INVALID')
    value = response['metadata']
    identity = value.get('table-uuid')
    try:
        require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'TABLE_UUID_INVALID')
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
            and type(value.get('current-schema-id')) is int and schema['schema-id'] == value['current-schema-id'],
            'TABLE_SCHEMA_CHANGED')
    fields = schema.get('fields')
    require(type(fields) is list and len(fields) == 4, 'TABLE_SCHEMA_CHANGED')
    ids = []
    for field, name, kind in zip(fields, C.COLUMNS, ('int', 'string', 'int', 'long')):
        require(type(field) is dict and field.get('name') == name and field.get('type') == kind
                and field.get('required') is False and type(field.get('id')) is int and field['id'] > 0,
                'TABLE_SCHEMA_CHANGED')
        ids.append(field['id'])
    require(len(set(ids)) == 4, 'TABLE_SCHEMA_CHANGED')
    specs = value.get('partition-specs')
    require(type(specs) is list and len(specs) == 1 and type(specs[0]) is dict
            and specs[0].get('fields') == [] and type(specs[0].get('spec-id')) is int
            and type(value.get('default-spec-id')) is int and specs[0]['spec-id'] == value['default-spec-id'],
            'TABLE_PARTITION_CHANGED')
    location = owned_path(response.get('metadata-location'), scope['location'] + '/metadata/', '.metadata.json')
    snapshots = value.get('snapshots', [])
    require(type(snapshots) is list and len(snapshots) <= 4, 'TABLE_SNAPSHOTS_INVALID')
    projected, known = [], set()
    for row in snapshots:
        require(type(row) is dict and type(row.get('summary')) is dict, 'TABLE_SNAPSHOTS_INVALID')
        snapshot, parent = sid(row.get('snapshot-id')), row.get('parent-snapshot-id')
        if parent is not None:
            sid(parent)
        require(snapshot not in known and snapshot != parent and type(row.get('sequence-number')) is int
                and row['sequence-number'] >= 0 and type(row.get('timestamp-ms')) is int
                and row['timestamp-ms'] >= 0, 'TABLE_SNAPSHOTS_INVALID')
        owned_path(row.get('manifest-list'), scope['location'] + '/metadata/', '.avro')
        summary = row['summary']
        require(summary.get('operation') == 'append', 'SNAPSHOT_OPERATION_INVALID')
        count, files = summary.get('total-records'), summary.get('total-data-files')
        require(count in ('0', '2', '3') and type(files) is str and re.fullmatch(r'[0-8]', files)
                and ((count == '0') == (files == '0')), 'SNAPSHOT_COUNTS_INVALID')
        projected.append({'snapshot_id': snapshot, 'parent_snapshot_id': parent,
                          'sequence_number': row['sequence-number'], 'operation': 'append',
                          'record_count': int(count), 'file_count': int(files), 'sha256': digest(row)})
        known.add(snapshot)
    current = value.get('current-snapshot-id')
    refs = value.get('refs', {})
    require(type(refs) is dict and set(refs) <= {'main', *REFS}, 'TABLE_REFS_INVALID')
    normalized = {}
    for name, ref in refs.items():
        require(type(ref) is dict and set(ref) == {'type', 'snapshot-id'}
                and ref['type'] == ('branch' if name == 'main' else REFS[name])
                and sid(ref['snapshot-id']) in known, 'TABLE_REFS_INVALID')
        normalized[name] = {'type': ref['type'], 'snapshot_id': ref['snapshot-id']}
    if snapshots:
        require(sid(current) in known and 'main' in refs and refs['main']['snapshot-id'] == current,
                'TABLE_MAIN_MISMATCH')
        parents = {s['snapshot_id']: s['parent_snapshot_id'] for s in projected}
        require(sum(parent is None for parent in parents.values()) == 1, 'SNAPSHOT_ROOT_INVALID')
        for snapshot in projected:
            parent, seen = snapshot['parent_snapshot_id'], {snapshot['snapshot_id']}
            while parent is not None:
                require(parent in parents and parent not in seen, 'SNAPSHOT_PARENT_INVALID')
                seen.add(parent)
                parent = parents[parent]
    else:
        require(current is None or type(current) is int and current == -1, 'EMPTY_SNAPSHOT_INVALID')
        require(not refs, 'TABLE_REFS_INVALID')
        current = None
    mutable = {'refs', 'last-updated-ms', 'metadata-log'}
    structure = {key: value.get(key) for key in ('table-uuid', 'location', 'format-version', 'properties',
        'schemas', 'current-schema-id', 'partition-specs', 'default-spec-id', 'sort-orders', 'default-sort-order-id')}
    return {'table_uuid': identity, 'table_location': scope['location'], 'marker': scope['marker'],
            'schema_sha256': digest(schemas), 'structure_sha256': digest(structure),
            'metadata_location': location, 'metadata_sha256': digest(value),
            'stable_metadata_sha256': digest({key: item for key, item in value.items() if key not in mutable}),
            'snapshot_id': current, 'refs': normalized,
            'snapshots': sorted(projected, key=lambda row: row['snapshot_id'])}


def rows(result, columns, types, expected):
    require(type(result) is dict and result.get('columns') == columns and result.get('column_types') == types
            and same(result.get('rows'), expected), 'TRINO_ORACLE_MISMATCH')
    return deepcopy(result['rows'])


def validate_files(value, location, records):
    require(type(value) is list and len(value) <= MAX_FILES and (bool(value) == (records > 0)), 'DATA_FILES_INVALID')
    paths = []
    for row in value:
        require(type(row) is dict and set(row) == {'path', 'record_count', 'file_size_in_bytes'}, 'DATA_FILE_INVALID')
        paths.append(owned_path(row['path'], location + '/data/', '.parquet'))
        require(type(row['record_count']) is int and 0 < row['record_count'] <= records
                and type(row['file_size_in_bytes']) is int and 0 < row['file_size_in_bytes'] <= MAX_FILE_BYTES,
                'DATA_FILE_INVALID')
    require(paths == sorted(set(paths)) and sum(row['record_count'] for row in value) == records
            and sum(row['file_size_in_bytes'] for row in value) <= MAX_SNAPSHOT_BYTES, 'DATA_FILES_ORACLE_MISMATCH')
    return deepcopy(value)


class HostTable:
    def __init__(self, token, worker, control, admin, progress=lambda: None):
        self.token, self.scope = token, names(token)
        self.worker, self.control, self.admin, self.progress = worker, control, admin, progress
        require(worker.user == SB.run_user(token) and control.user == SB.run_user(token, True), 'QUERY_IDENTITIES_DIFFER')
        require(getattr(admin, 'run_token', None) == token and callable(getattr(admin, 'commit_reference', None)),
                'ADMIN_IDENTITY_OR_COMMIT_SCOPE_DIFFER')
        self.deadline = time.monotonic() + 900
        self.journal = {'created': False, 'create_attempted': False, 'drop_attempted': False,
                        'prepare_attempted': False, 'identity': None, 'pending': None, 'uncertain': False,
                        'steps': [], 'ref_commits': [], 'sealed': None, 'spark_outcome': 'not-started',
                        'cleanup': {'verified': False}}

    def receipt(self):
        return deepcopy(self.journal)

    def _save_preserving(self, error):
        try:
            self.progress()
        except BaseException:
            if not isinstance(error, Exception):
                raise error
            raise

    def sql(self, label, sql, *, control=False, mutation=False):
        require(len(self.journal['steps']) < 80, 'QUERY_LIMIT')
        require(not mutation or not self.journal['uncertain'] and self.journal['pending'] is None,
                'MUTATION_OUTCOME_UNKNOWN')
        record = {'label': label, 'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
                  'query_id': None, 'outcome': 'not-sent', 'mutation': mutation}
        self.journal['steps'].append(record)
        self.progress()  # failure here is definitely before the SQL call
        if mutation:
            self.journal['pending'] = label
            self.journal['uncertain'] = True
        record['outcome'] = 'pending'
        try:
            self.progress()
        except BaseException:
            record['outcome'] = 'not-sent'
            if mutation:
                self.journal.update(pending=None, uncertain=False)
            raise
        try:
            result = SB.bounded_query(self.control if control else self.worker, sql, self.deadline)
            record.update(query_id=result['query_id'], outcome='verified')
            if mutation:
                self.journal.update(pending=None, uncertain=False)
            self.progress()
            return result
        except BaseException as error:
            record['outcome'] = 'unknown'
            if mutation:
                self.journal.update(pending=label, uncertain=True)
            self._save_preserving(error)
            raise

    def load(self):
        original = self.journal['identity']
        value = project(self.admin.request('GET', self.scope['path']), self.token,
                        original['table_uuid'] if original else None)
        if original:
            require(value['structure_sha256'] == original['structure_sha256'], 'TABLE_STRUCTURE_CHANGED')
        return value

    def guard(self):
        require(self.journal['created'] and self.journal['identity'] is not None, 'ORIGINAL_TABLE_UUID_UNKNOWN')
        return self.load()

    def _read_rows(self, label, ref, expected):
        require(ref is None or type(ref) is int and 0 < ref < 2**63 or ref in REFS, 'READ_REFERENCE_INVALID')
        suffix = '' if ref is None else ' FOR VERSION AS OF ' + (str(ref) if type(ref) is int else "'" + ref + "'")
        return rows(self.sql(label, f'SELECT id, name, bucket, amount FROM {self.scope["table"]}{suffix} ORDER BY id'),
                    C.COLUMNS, C.COLUMN_TYPES, expected)

    def files(self, label, expected_records):
        result = self.sql(label, 'SELECT file_path, record_count, file_size_in_bytes FROM '
                          f'iceberg.local_experiment."{self.scope["name"]}$files" ORDER BY file_path')
        require(result['columns'] == ['file_path', 'record_count', 'file_size_in_bytes']
                and result['column_types'] == ['varchar', 'bigint', 'bigint']
                and type(result['rows']) is list and all(type(row) is list and len(row) == 3 for row in result['rows']),
                'DATA_FILES_INVALID')
        value = [dict(zip(('path', 'record_count', 'file_size_in_bytes'), row)) for row in result['rows']]
        return validate_files(value, self.scope['location'], expected_records)

    def _inspect_sql_metadata(self, current):
        rows(self.sql('trino_refs', 'SELECT name, "type", snapshot_id FROM '
             f'iceberg.local_experiment."{self.scope["name"]}$refs" ORDER BY name'),
             ['name', 'type', 'snapshot_id'], ['varchar', 'varchar', 'bigint'],
             [[name, ref['type'].upper(), ref['snapshot_id']] for name, ref in sorted(current['refs'].items())])
        rows(self.sql('trino_snapshots', 'SELECT snapshot_id, parent_id, operation FROM '
             f'iceberg.local_experiment."{self.scope["name"]}$snapshots" ORDER BY snapshot_id'),
             ['snapshot_id', 'parent_id', 'operation'], ['bigint', 'bigint', 'varchar'],
             [[row['snapshot_id'], row['parent_snapshot_id'], row['operation']] for row in current['snapshots']])

    def create(self):
        require(not self.journal['create_attempted'], 'TABLE_CREATE_RETRY_FORBIDDEN')
        inventory = self.sql('absence', 'SHOW TABLES FROM iceberg.local_experiment')
        require(type(inventory.get('rows')) is list and [self.scope['name']] not in inventory['rows'], 'TABLE_ALREADY_EXISTS')
        self.journal['create_attempted'] = True
        self.progress()
        self.sql('create', f'CREATE TABLE {self.scope["table"]} (id INTEGER, name VARCHAR, bucket INTEGER, amount BIGINT) '
                 f"COMMENT '{self.scope['marker']}' WITH (format = 'PARQUET', format_version = 2, location = '{self.scope['location']}')",
                 mutation=True)
        identity = self.load()
        self.journal.update(created=True, identity=identity)
        self.progress()
        require(len(identity['snapshots']) <= 1 and all(row['record_count'] == row['file_count'] == 0
                and row['parent_snapshot_id'] is None for row in identity['snapshots']), 'TABLE_NOT_EMPTY')
        self._read_rows('empty_rows', None, [])
        self.files('empty_files', 0)
        require(same(self.guard(), identity), 'EMPTY_TABLE_CHANGED')
        return identity

    def _append(self, label, values, expected_rows, before):
        require(same(self.guard(), before), 'PRE_APPEND_METADATA_CHANGED')
        fixed = ', '.join("(%d, '%s', %d, %d)" % tuple(row) for row in values)
        self.sql(label, f'INSERT INTO {self.scope["table"]} VALUES {fixed}', mutation=True)
        after = self.guard()
        existing = {row['snapshot_id'] for row in before['snapshots']}
        added = [row for row in after['snapshots'] if row['snapshot_id'] not in existing]
        require(len(after['snapshots']) == len(before['snapshots']) + 1 and len(added) == 1
                and all(row in after['snapshots'] for row in before['snapshots'])
                and added[0]['parent_snapshot_id'] == before['snapshot_id']
                and added[0]['snapshot_id'] == after['snapshot_id'] and added[0]['record_count'] == len(expected_rows),
                'HOST_APPEND_SNAPSHOT_INVALID')
        require(after['refs'] == {**before['refs'], 'main': {'type': 'branch', 'snapshot_id': after['snapshot_id']}},
                'HOST_APPEND_REFS_CHANGED')
        self._read_rows(label + '_rows', None, expected_rows)
        return after

    def _set_ref(self, before, name):
        require(name in REFS and name not in before['refs'] and not self.journal['uncertain']
                and self.journal['pending'] is None and len(self.journal['ref_commits']) < 2, 'REF_MUTATION_FORBIDDEN')
        require(same(self.guard(), before), 'STALE_METADATA_BEFORE_REF')
        body = {'requirements': [{'type': 'assert-table-uuid', 'uuid': before['table_uuid']},
            {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': before['snapshot_id']},
            {'type': 'assert-ref-snapshot-id', 'ref': name, 'snapshot-id': None}],
            'updates': [{'action': 'set-snapshot-ref', 'ref-name': name, 'type': REFS[name], 'snapshot-id': before['snapshot_id']}]}
        validate_ref_body(body, self.token)
        op = {'operation_id': str(uuid.uuid4()), 'ref_name': name, 'request_sha256': digest(body),
              'before_metadata_sha256': before['metadata_sha256'], 'outcome': 'not-sent', 'http_status': None}
        self.journal['ref_commits'].append(op)
        self.progress()
        self.journal.update(pending=op['operation_id'], uncertain=True)
        op['outcome'] = 'pending'
        try:
            self.progress()
        except BaseException:
            self.journal.update(pending=None, uncertain=False)
            op['outcome'] = 'not-sent'
            raise
        try:
            result = self.admin.commit_reference(body)
            op['http_status'] = 200
            committed = project(result, self.token, before['table_uuid'])
            expected = {**before['refs'], name: {'type': REFS[name], 'snapshot_id': before['snapshot_id']}}
            require(committed['refs'] == expected and committed['stable_metadata_sha256'] == before['stable_metadata_sha256'],
                    'REF_CHANGED_NON_REFERENCE_METADATA')
            after = self.guard()
            require(same(after, committed), 'REF_READBACK_DIFFER')
            op.update(outcome='verified', after_metadata_sha256=after['metadata_sha256'])
            self.journal.update(pending=None, uncertain=False)
            self.progress()
            return after
        except BaseException as error:
            rejected = getattr(error, 'outcome', None) == 'rejected' and getattr(error, 'http_status', None) in (400, 401, 403, 404, 405, 409)
            op.update(outcome='rejected' if rejected else 'unknown', http_status=getattr(error, 'http_status', None) or op['http_status'])
            self.journal.update(pending=None if rejected else op['operation_id'], uncertain=not rejected)
            try:
                self._save_preserving(error)
            except BaseException:
                self.journal.update(pending=op['operation_id'], uncertain=True)
                op['outcome'] = 'unknown'
                raise
            if not isinstance(error, Exception):
                raise
            raise TableError('REF_COMMIT_REJECTED' if rejected else 'REF_COMMIT_UNKNOWN') from None

    def prepare(self):
        require(not self.journal['prepare_attempted'], 'PREPARE_RETRY_FORBIDDEN')
        self.journal['prepare_attempted'] = True
        self.progress()
        empty = self.create()
        a = self._append('append_a', C.ROWS_A, C.ROWS_A, empty)
        files_a = self.files('files_a', 2)
        require(len(files_a) == next(s['file_count'] for s in a['snapshots'] if s['snapshot_id'] == a['snapshot_id']), 'DATA_FILE_COUNT_DIFFER')
        tag = self._set_ref(a, 'baseline_tag')
        require(self.files('files_after_tag', 2) == files_a, 'REF_CHANGED_DATA_FILES')
        branch = self._set_ref(tag, 'audit_branch')
        require(self.files('files_after_branch', 2) == files_a, 'REF_CHANGED_DATA_FILES')
        b = self._append('append_b', [C.MAIN_ROW], C.ROWS_B, branch)
        files_b = self.files('files_b', 3)
        require(all(row in files_b for row in files_a) and len(files_b) > len(files_a), 'MAIN_APPEND_FILES_INVALID')
        require(same(self.guard(), b), 'PREPARE_METADATA_CHANGED')
        seal = {'identity': {key: b[key] for key in ('table_uuid', 'table_location', 'marker', 'schema_sha256',
                'metadata_location', 'metadata_sha256')}, 'initial_snapshot_id': empty['snapshot_id'],
                'snapshot_a': a['snapshot_id'], 'snapshot_b': b['snapshot_id'], 'refs': b['refs'],
                'snapshots': b['snapshots'], 'data_files': {'a': files_a, 'b': files_b}}
        C.validate_sealed(seal, {'table_uuid': b['table_uuid'], 'table_location': b['table_location'],
                                'initial_snapshot_id': b['snapshot_id']}, self.token)
        self.journal['sealed'] = seal
        self._inspect_sql_metadata(b)
        self._read_rows('tag_before_spark', 'baseline_tag', C.ROWS_A)
        self._read_rows('branch_before_spark', 'audit_branch', C.ROWS_A)
        self._read_rows('snapshot_a_before_spark', a['snapshot_id'], C.ROWS_A)
        self._read_rows('snapshot_b_before_spark', b['snapshot_id'], C.ROWS_B)
        self.guard_seal()
        self.progress()
        return deepcopy(seal)

    def guard_seal(self):
        seal = self.journal['sealed']
        require(seal is not None and self.journal['spark_outcome'] == 'not-started', 'SEALED_TABLE_UNAVAILABLE')
        current = self.guard()
        require(current['metadata_sha256'] == seal['identity']['metadata_sha256']
                and current['metadata_location'] == seal['identity']['metadata_location']
                and same(current['refs'], seal['refs']) and same(current['snapshots'], seal['snapshots']), 'SEALED_TABLE_CHANGED')
        require(self.files('guard_main_files', 3) == seal['data_files']['b'], 'SEALED_FILES_CHANGED')
        require(same(self.guard(), current), 'SEALED_TABLE_CHANGED')
        return deepcopy(seal)

    def mark_spark_pending(self):
        self.guard_seal()
        require(not self.journal['uncertain'] and self.journal['pending'] is None, 'MUTATION_OUTCOME_UNKNOWN')
        self.journal.update(spark_outcome='unknown', pending='spark_branch_append', uncertain=True)
        try:
            self.progress()
        except BaseException:
            # The caller must not launch native Spark unless this method returns.
            self.journal.update(spark_outcome='not-started', pending=None, uncertain=False)
            raise

    def verify_written(self, spark):
        require(self.journal['spark_outcome'] == 'unknown' and self.journal['pending'] == 'spark_branch_append', 'SPARK_NOT_PENDING')
        require(type(spark) is dict and spark.get('result') == 'passed' and spark.get('write_outcome') == 'verified'
                and type(spark.get('write_attempts')) is int and spark['write_attempts'] == 1, 'SPARK_WRITE_UNCONFIRMED')
        seal = self.journal['sealed']
        C.validate_before(spark.get('before'), seal)
        current = self.guard()
        a, b = seal['snapshot_a'], seal['snapshot_b']
        refs = current['refs']
        require(set(refs) == {'main', *REFS} and refs['main'] == {'type': 'branch', 'snapshot_id': b}
                and refs['baseline_tag'] == {'type': 'tag', 'snapshot_id': a}, 'MAIN_OR_TAG_CHANGED')
        c = sid(refs['audit_branch']['snapshot_id'])
        require(c not in {s['snapshot_id'] for s in seal['snapshots']} and current['snapshot_id'] == b,
                'BRANCH_SNAPSHOT_INVALID')
        added = [row for row in current['snapshots'] if row['snapshot_id'] == c]
        require(len(current['snapshots']) == len(seal['snapshots']) + 1
                and all(row in current['snapshots'] for row in seal['snapshots']) and len(added) == 1
                and added[0]['parent_snapshot_id'] == a and added[0]['record_count'] == 3
                and added[0]['sequence_number'] > max(s['sequence_number'] for s in seal['snapshots']),
                'BRANCH_SIBLING_LINEAGE_INVALID')
        expected_identity = {'table_uuid': current['table_uuid'], 'table_location': current['table_location'],
            'marker': current['marker'], 'metadata_location': current['metadata_location'], 'main_snapshot_id': b,
            'tag_snapshot_id': a, 'branch_snapshot_id': c, 'parent_snapshot_id': a}
        require(same(spark.get('table_identity'), expected_identity), 'SPARK_TABLE_IDENTITY_DIFFER')
        summaries = [{key: value for key, value in row.items() if key != 'sha256'} for row in current['snapshots']]
        require(same(spark.get('snapshot_summaries'), summaries), 'SPARK_SNAPSHOT_SUMMARIES_DIFFER')
        wanted_rows = {'main': C.ROWS_B, 'baseline_tag': C.ROWS_A, 'audit_branch': C.ROWS_C}
        require(same(spark.get('dataframe_rows'), wanted_rows) and same(spark.get('sql_rows'), wanted_rows), 'SPARK_ROWS_DIFFER')
        self._inspect_sql_metadata(current)
        observed = {}
        for name, ref, oracle in [('main', None, C.ROWS_B), ('baseline_tag', 'baseline_tag', C.ROWS_A),
                                  ('audit_branch', 'audit_branch', C.ROWS_C), ('snapshot_a', a, C.ROWS_A),
                                  ('snapshot_b', b, C.ROWS_B), ('snapshot_c', c, C.ROWS_C)]:
            observed[name] = self._read_rows(name + '_after_spark', ref, oracle)
        files = self.files('main_files_after_spark', 3)
        require(files == seal['data_files']['b'], 'MAIN_FILES_CHANGED')
        inventories = spark.get('snapshot_files')
        require(type(inventories) is dict and set(inventories) == {'a', 'b', 'c'}, 'SPARK_FILES_INVALID')
        for key, count in [('a', 2), ('b', 3), ('c', 3)]:
            validate_files(inventories[key], self.scope['location'], count)
        require(same(inventories['a'], seal['data_files']['a']) and same(inventories['b'], files)
                and all(row in inventories['c'] for row in inventories['a'])
                and len(inventories['c']) == added[0]['file_count'], 'SPARK_FILES_DIFFER')
        delta_b = {row['path'] for row in files} - {row['path'] for row in inventories['a']}
        delta_c = {row['path'] for row in inventories['c']} - {row['path'] for row in inventories['a']}
        require(delta_b and delta_c and delta_b.isdisjoint(delta_c), 'BRANCH_FILES_NOT_INDEPENDENT')
        require(same(self.guard(), current), 'POST_VERIFY_METADATA_CHANGED')
        verified = {'verified': True, 'identity': expected_identity, 'snapshots': summaries,
                    'rows': observed, 'snapshot_files': deepcopy(inventories), 'main_files_unchanged': True,
                    'sibling_parents_verified': True}
        self.journal['verification'] = verified
        self.journal.update(spark_outcome='verified', pending=None, uncertain=False)
        try:
            self.progress()
        except BaseException:
            self.journal.update(spark_outcome='unknown', pending='spark_branch_append', uncertain=True)
            raise
        return deepcopy(verified)

    def cleanup(self, *, writers_stopped, queries_drained):
        require(writers_stopped and queries_drained, 'WRITERS_OR_QUERIES_NOT_DRAINED')
        self.deadline = time.monotonic() + 90
        if self.journal['uncertain'] or self.journal['pending'] is not None:
            self.journal['cleanup'] = {'verified': False, 'deferred': ['MUTATION_OUTCOME_UNKNOWN']}
        elif not self.journal['create_attempted']:
            self.journal['cleanup'] = {'verified': True, 'action': 'not-created'}
        elif not self.journal['created'] or self.journal['identity'] is None:
            self.journal['cleanup'] = {'verified': False, 'deferred': ['ORIGINAL_TABLE_UUID_UNKNOWN']}
        else:
            self.guard()
            require(not self.journal['drop_attempted'], 'TABLE_DROP_RETRY_FORBIDDEN')
            self.journal['drop_attempted'] = True
            self.progress()
            self.sql('drop', 'DROP TABLE ' + self.scope['table'], control=True, mutation=True)
            remaining = self.sql('absence_after_drop', 'SHOW TABLES FROM iceberg.local_experiment', control=True)
            require(type(remaining.get('rows')) is list and [self.scope['name']] not in remaining['rows'], 'TABLE_CLEANUP_UNVERIFIED')
            self.journal['cleanup'] = {'verified': True, 'action': 'same-uuid-drop', 'table_uuid': self.journal['identity']['table_uuid']}
        self.progress()
        return deepcopy(self.journal['cleanup'])
