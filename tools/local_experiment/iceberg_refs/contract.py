"""Fixed owned-table reference contract; no HTTP, SQL execution, or secrets.

Iceberg 1.11 REST requirements and updates are the protocol used by Polaris 1.6:
https://github.com/apache/iceberg/blob/apache-iceberg-1.11.0/open-api/rest-catalog-open-api.yaml
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

CATALOG = 'shirokuma_l1'
NAMESPACE = 'local_experiment'
PORT = 18181
REFS = {'baseline_tag': 'tag', 'audit_branch': 'branch'}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOTS = 16
MAX_FILES = 32


class ContractError(ValueError):
    pass


def require(value, code):
    if not value:
        raise ContractError(code)


def digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ContractError('INVALID_JSON_CONTENT') from None


def table_name(token):
    require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token), 'INVALID_RUN_TOKEN')
    return 'refs_' + token


def marker(token):
    table_name(token)
    return 'shirokuma-local-iceberg-refs:' + token


def table_path(token):
    return f'/api/catalog/v1/{CATALOG}/namespaces/{NAMESPACE}/tables/{table_name(token)}'


def canonical_uuid(value):
    require(type(value) is str, 'INVALID_TABLE_UUID')
    try:
        require(str(uuid.UUID(value)) == value, 'INVALID_TABLE_UUID')
    except (ValueError, AttributeError):
        raise ContractError('INVALID_TABLE_UUID') from None
    return value


def integer(value, code='INVALID_INTEGER', *, minimum=0):
    require(type(value) is int and minimum <= value <= 2**63 - 1, code)
    return value


def snapshot_id(value):
    return integer(value, 'INVALID_SNAPSHOT_ID', minimum=1)


def _text(value, code, limit=4096):
    require(type(value) is str and 0 < len(value) <= limit and
            not any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF
                    for char in value), code)
    return value


def project_metadata(response, token, expected_uuid=None):
    """Project bounded metadata; file paths/config/auth values never leave this boundary.

    Snapshot metadata describes manifests, not their data-file contents. Consumers
    must compare project_data_files() separately before claiming unchanged files.
    """
    table = table_name(token)
    require(type(response) is dict and {'metadata', 'metadata-location'} <= set(response)
            and set(response) <= {'metadata', 'metadata-location', 'config', 'storage-credentials'},
            'INVALID_TABLE_RESPONSE')
    metadata = response['metadata']
    require(type(metadata) is dict and len(metadata) <= 40, 'INVALID_TABLE_METADATA')
    location = _text(response['metadata-location'], 'INVALID_METADATA_LOCATION')
    table_uuid = canonical_uuid(metadata.get('table-uuid'))
    if expected_uuid is not None:
        require(table_uuid == canonical_uuid(expected_uuid), 'TABLE_UUID_CHANGED')
    require(type(metadata.get('format-version')) is int and metadata['format-version'] == 2,
            'UNSUPPORTED_FORMAT_VERSION')
    _text(metadata.get('location'), 'INVALID_TABLE_LOCATION')
    properties = metadata.get('properties')
    require(type(properties) is dict and len(properties) <= 64 and
            properties.get('comment') == marker(token), 'TABLE_OWNERSHIP_MISMATCH')
    for key, value in properties.items():
        _text(key, 'INVALID_TABLE_PROPERTIES', 256)
        require(type(value) is str and len(value) <= 4096, 'INVALID_TABLE_PROPERTIES')
    schemas = metadata.get('schemas')
    require(type(schemas) is list and len(schemas) == 1, 'UNSUPPORTED_TABLE_SCHEMA')
    schema = schemas[0]
    require(type(schema) is dict and schema.get('type') == 'struct' and
            type(schema.get('schema-id')) is int and schema['schema-id'] >= 0 and
            type(metadata.get('current-schema-id')) is int and
            metadata['current-schema-id'] == schema['schema-id'], 'UNSUPPORTED_TABLE_SCHEMA')
    fields = schema.get('fields')
    require(type(fields) is list and len(fields) == 2, 'UNSUPPORTED_TABLE_SCHEMA')
    ids = []
    for field, name, kind in zip(fields, ('id', 'name'), ('long', 'string')):
        require(type(field) is dict and field.get('name') == name and field.get('type') == kind and
                type(field.get('required')) is bool, 'UNSUPPORTED_TABLE_SCHEMA')
        ids.append(integer(field.get('id'), 'UNSUPPORTED_TABLE_SCHEMA', minimum=1))
    require(len(set(ids)) == 2, 'UNSUPPORTED_TABLE_SCHEMA')
    snapshots = metadata.get('snapshots')
    require(type(snapshots) is list and 1 <= len(snapshots) <= MAX_SNAPSHOTS, 'INVALID_SNAPSHOTS')
    projected, known = [], set()
    for snapshot in snapshots:
        require(type(snapshot) is dict and len(snapshot) <= 12, 'INVALID_SNAPSHOT')
        sid = snapshot_id(snapshot.get('snapshot-id'))
        require(sid not in known, 'DUPLICATE_SNAPSHOT')
        known.add(sid)
        parent = snapshot.get('parent-snapshot-id')
        if parent is not None:
            snapshot_id(parent)
            require(parent != sid, 'INVALID_SNAPSHOT_PARENT')
        integer(snapshot.get('sequence-number'), 'INVALID_SEQUENCE_NUMBER')
        integer(snapshot.get('timestamp-ms'), 'INVALID_SNAPSHOT_TIMESTAMP')
        _text(snapshot.get('manifest-list'), 'INVALID_MANIFEST_LOCATION')
        summary = snapshot.get('summary')
        require(type(summary) is dict and len(summary) <= 64 and summary.get('operation') == 'append',
                'UNSUPPORTED_SNAPSHOT_OPERATION')
        require(all(type(key) is str and type(value) is str and len(key) <= 256 and len(value) <= 4096
                    for key, value in summary.items()), 'INVALID_SNAPSHOT_SUMMARY')
        projected.append({'snapshot_id': sid, 'parent_snapshot_id': parent,
                          'sequence_number': snapshot['sequence-number'],
                          'manifest_list_sha256': digest(snapshot['manifest-list'])})
    for snapshot in projected:
        require(snapshot['parent_snapshot_id'] is None or snapshot['parent_snapshot_id'] in known,
                'MISSING_PARENT_SNAPSHOT')
    current = snapshot_id(metadata.get('current-snapshot-id'))
    require(current in known, 'CURRENT_SNAPSHOT_MISSING')
    refs = metadata.get('refs')
    require(type(refs) is dict and 'main' in refs and set(refs) <= {'main', *REFS}, 'UNEXPECTED_REFERENCE')
    safe_refs = {}
    for name, ref in refs.items():
        # This fresh-table experiment never sets retention. Ref retention is
        # semantic metadata, so reject it rather than silently projecting it out.
        require(type(ref) is dict and set(ref) == {'type', 'snapshot-id'},
                'INVALID_REFERENCE')
        require(ref['type'] == ('branch' if name == 'main' else REFS[name]), 'REFERENCE_TYPE_MISMATCH')
        sid = snapshot_id(ref['snapshot-id'])
        require(sid in known, 'REFERENCE_SNAPSHOT_MISSING')
        safe_refs[name] = {'type': ref['type'], 'snapshot_id': sid}
    require(safe_refs['main']['snapshot_id'] == current, 'MAIN_REFERENCE_MISMATCH')
    stable = {key: value for key, value in metadata.items()
              if key not in {'refs', 'last-updated-ms', 'metadata-log'}}
    return {'schema_version': 1, 'run_token': token, 'table': table, 'table_uuid': table_uuid,
            'metadata_sha256': digest(metadata), 'metadata_location_sha256': digest(location),
            'table_location_sha256': digest(metadata['location']),
            'stable_metadata_sha256': digest(stable), 'schema_sha256': digest(schemas),
            'snapshots_sha256': digest(snapshots), 'current_snapshot_id': current,
            'refs': safe_refs, 'snapshots': sorted(projected, key=lambda item: item['snapshot_id']),
            'data_files_verified': False}


def validate_projection(value, token):
    require(type(value) is dict and set(value) == {'schema_version', 'run_token', 'table', 'table_uuid',
        'metadata_sha256', 'metadata_location_sha256', 'table_location_sha256', 'stable_metadata_sha256', 'schema_sha256',
        'snapshots_sha256', 'current_snapshot_id', 'refs', 'snapshots', 'data_files_verified'},
        'INVALID_METADATA_PROJECTION')
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and
            value['run_token'] == token and value['table'] == table_name(token) and
            value['data_files_verified'] is False, 'INVALID_METADATA_PROJECTION')
    canonical_uuid(value['table_uuid'])
    for key in ('metadata_sha256', 'metadata_location_sha256', 'table_location_sha256', 'stable_metadata_sha256',
                'schema_sha256', 'snapshots_sha256'):
        require(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key]), 'INVALID_METADATA_HASH')
    snapshot_id(value['current_snapshot_id'])
    snapshots = value['snapshots']
    require(type(snapshots) is list and 1 <= len(snapshots) <= MAX_SNAPSHOTS, 'INVALID_SNAPSHOTS')
    known = set()
    for snapshot in snapshots:
        require(type(snapshot) is dict and set(snapshot) == {'snapshot_id', 'parent_snapshot_id',
            'sequence_number', 'manifest_list_sha256'}, 'INVALID_SNAPSHOT')
        sid = snapshot_id(snapshot['snapshot_id'])
        require(sid not in known, 'DUPLICATE_SNAPSHOT')
        known.add(sid)
        integer(snapshot['sequence_number'])
        if snapshot['parent_snapshot_id'] is not None:
            snapshot_id(snapshot['parent_snapshot_id'])
        require(type(snapshot['manifest_list_sha256']) is str and
                re.fullmatch('[0-9a-f]{64}', snapshot['manifest_list_sha256']), 'INVALID_MANIFEST_HASH')
    refs = value['refs']
    require(type(refs) is dict and 'main' in refs and set(refs) <= {'main', *REFS}, 'UNEXPECTED_REFERENCE')
    for name, ref in refs.items():
        require(type(ref) is dict and set(ref) == {'type', 'snapshot_id'} and
                ref['type'] == ('branch' if name == 'main' else REFS[name]) and
                type(ref['snapshot_id']) is int and ref['snapshot_id'] in known, 'INVALID_REFERENCE')
    require(refs['main']['snapshot_id'] == value['current_snapshot_id'], 'MAIN_REFERENCE_MISMATCH')
    return value


def commit_body(before, token, ref_name, target_snapshot, expected_ref):
    validate_projection(before, token)
    require(type(ref_name) is str and ref_name in REFS, 'REF_NAME_NOT_ALLOWED')
    snapshot_id(target_snapshot)
    require(target_snapshot in {item['snapshot_id'] for item in before['snapshots']}, 'TARGET_SNAPSHOT_MISSING')
    if expected_ref is not None:
        snapshot_id(expected_ref)
    require(ref_name != 'baseline_tag' or expected_ref is None, 'TAG_UPDATE_NOT_ALLOWED')
    return {'requirements': [
        {'type': 'assert-table-uuid', 'uuid': before['table_uuid']},
        {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': before['current_snapshot_id']},
        {'type': 'assert-ref-snapshot-id', 'ref': ref_name, 'snapshot-id': expected_ref}],
        'updates': [{'action': 'set-snapshot-ref', 'ref-name': ref_name,
                     'type': REFS[ref_name], 'snapshot-id': target_snapshot}]}


def verify_transition(before, after, token, ref_name, target_snapshot):
    validate_projection(before, token)
    validate_projection(after, token)
    require(before['table_uuid'] == after['table_uuid'] and
            before['stable_metadata_sha256'] == after['stable_metadata_sha256'], 'NON_REFERENCE_METADATA_CHANGED')
    expected = {**before['refs'], ref_name: {'type': REFS[ref_name], 'snapshot_id': target_snapshot}}
    require(after['refs'] == expected, 'REFERENCE_READBACK_MISMATCH')
    return after


def project_data_files(result):
    """Fixed Trino $files projection; raw paths are replaced with hashes."""
    require(type(result) is dict and result.get('columns') == ['file_path', 'record_count', 'file_size_in_bytes'],
            'INVALID_DATA_FILES_RESULT')
    rows = result.get('rows')
    require(type(rows) is list and 1 <= len(rows) <= MAX_FILES, 'INVALID_DATA_FILE_COUNT')
    projected, seen = [], set()
    for row in rows:
        require(type(row) is list and len(row) == 3, 'INVALID_DATA_FILE')
        path = _text(row[0], 'INVALID_DATA_FILE_PATH')
        require(path not in seen, 'DUPLICATE_DATA_FILE')
        seen.add(path)
        projected.append({'path_sha256': digest(path), 'record_count': integer(row[1]),
                          'file_size_in_bytes': integer(row[2])})
    projected.sort(key=lambda row: row['path_sha256'])
    return {'file_count': len(projected), 'record_count': sum(row['record_count'] for row in projected),
            'file_size_in_bytes': sum(row['file_size_in_bytes'] for row in projected),
            'files_sha256': digest(projected), 'files': projected}
