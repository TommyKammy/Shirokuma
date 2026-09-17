"""Fixed main-publication proof; original branch/ref contracts stay unchanged.

A generic REST set-snapshot-ref does not enforce fast-forward ancestry. This
module proves ancestry before constructing one UUID/main/branch/tag CAS body.
Only the current-main pointer and its required metadata history may change.
"""
from copy import deepcopy
import re
import uuid

from tools.local_experiment.spark_branch import table as B
from tools.local_experiment.trino_opa.common import digest

C, REFS = B.C, B.REFS
names, owned_path, sid, require = B.names, B.owned_path, B.sid, B.require
TableError = B.TableError

def _project(response, token, expected_uuid=None):
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
    require(type(snapshots) is list and len(snapshots) <= 5, 'TABLE_SNAPSHOTS_INVALID')
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
        require(count in ('0', '2', '3', '4') and type(files) is str and re.fullmatch(r'[0-8]', files)
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


def project(response, token, expected_uuid=None):
    value = _project(response, token, expected_uuid)
    meta = response['metadata']
    updated = meta.get('last-updated-ms')
    require(type(updated) is int and 0 <= updated < 2**63, 'METADATA_TIME_INVALID')
    sequence = meta.get('last-sequence-number')
    require(type(sequence) is int and 0 <= sequence < 2**63
            and sequence == max((s['sequence_number'] for s in value['snapshots']), default=0),
            'METADATA_SEQUENCE_INVALID')
    snapshot_log, metadata_log = meta.get('snapshot-log', []), meta.get('metadata-log', [])
    require(type(snapshot_log) is list and len(snapshot_log) <= 8
            and type(metadata_log) is list and len(metadata_log) <= 16, 'METADATA_HISTORY_INVALID')
    known = {s['snapshot_id'] for s in value['snapshots']}
    for row in snapshot_log:
        require(type(row) is dict and set(row) == {'timestamp-ms', 'snapshot-id'}
                and type(row['timestamp-ms']) is int and 0 <= row['timestamp-ms'] <= updated
                and type(row['snapshot-id']) is int and row['snapshot-id'] in known,
                'SNAPSHOT_LOG_INVALID')
    require([r['timestamp-ms'] for r in snapshot_log] == sorted(r['timestamp-ms'] for r in snapshot_log)
            and (value['snapshot_id'] is None and not snapshot_log or snapshot_log
                 and snapshot_log[-1]['snapshot-id'] == value['snapshot_id']), 'SNAPSHOT_LOG_INVALID')
    for row in metadata_log:
        require(type(row) is dict and set(row) == {'timestamp-ms', 'metadata-file'}
                and type(row['timestamp-ms']) is int and 0 <= row['timestamp-ms'] <= updated,
                'METADATA_LOG_INVALID')
        owned_path(row['metadata-file'], value['table_location'] + '/metadata/', '.metadata.json')
    require([r['timestamp-ms'] for r in metadata_log] == sorted(r['timestamp-ms'] for r in metadata_log)
            and len({r['metadata-file'] for r in metadata_log}) == len(metadata_log), 'METADATA_LOG_INVALID')
    mutable = {'refs', 'current-snapshot-id', 'snapshot-log', 'metadata-log', 'last-updated-ms'}
    value.update(last_updated_ms=updated, last_sequence_number=sequence,
                 snapshot_log=deepcopy(snapshot_log), metadata_log=deepcopy(metadata_log),
                 publish_stable_sha256=digest({k: v for k, v in meta.items() if k not in mutable}))
    return value


def ancestry(before, main, branch):
    """Return target-to-main IDs, or None for a known non-ancestor; never infer gaps."""
    sid(main), sid(branch)
    snapshots = before['snapshots']
    require(type(snapshots) is list and 1 <= len(snapshots) <= 5, 'PUBLISH_LINEAGE_UNKNOWN')
    parents = {r['snapshot_id']: r['parent_snapshot_id'] for r in snapshots}
    require(len(parents) == len(snapshots) and main in parents and branch in parents, 'PUBLISH_LINEAGE_UNKNOWN')
    chain, current = [], branch
    while current is not None:
        require(current in parents and current not in chain and len(chain) < 5, 'PUBLISH_LINEAGE_UNKNOWN')
        chain.append(current)
        if current == main:
            return chain
        current = parents[current]
    return None


def commit_body(before, *, expected_main, branch, tag):
    refs = before['refs']
    require(set(refs) == {'main', 'audit_branch', 'baseline_tag'}
            and refs == {'main': {'type': 'branch', 'snapshot_id': sid(expected_main)},
                         'audit_branch': {'type': 'branch', 'snapshot_id': sid(branch)},
                         'baseline_tag': {'type': 'tag', 'snapshot_id': sid(tag)}}
            and len({expected_main, branch, tag}) == 3
            and before['snapshot_id'] == expected_main, 'PUBLISH_REFS_CHANGED')
    chain = ancestry(before, expected_main, branch)
    require(chain is not None and len(chain) >= 2, 'MAIN_NOT_ANCESTOR')
    return {'requirements': [
        {'type': 'assert-table-uuid', 'uuid': before['table_uuid']},
        {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': expected_main},
        {'type': 'assert-ref-snapshot-id', 'ref': 'audit_branch', 'snapshot-id': branch},
        {'type': 'assert-ref-snapshot-id', 'ref': 'baseline_tag', 'snapshot-id': tag}],
        'updates': [{'action': 'set-snapshot-ref', 'ref-name': 'main', 'type': 'branch', 'snapshot-id': branch}]}


def validate_body(body, token):
    names(token)
    require(type(body) is dict and set(body) == {'requirements', 'updates'}, 'PUBLISH_BODY_INVALID')
    req, updates = body['requirements'], body['updates']
    require(type(req) is list and len(req) == 4 and type(updates) is list and len(updates) == 1
            and all(type(r) is dict for r in req + updates), 'PUBLISH_BODY_INVALID')
    require(set(req[0]) == {'type', 'uuid'} and req[0]['type'] == 'assert-table-uuid', 'PUBLISH_BODY_INVALID')
    try:
        require(type(req[0]['uuid']) is str and str(uuid.UUID(req[0]['uuid'])) == req[0]['uuid'], 'PUBLISH_BODY_INVALID')
    except (ValueError, AttributeError):
        raise TableError('PUBLISH_BODY_INVALID') from None
    ids = []
    for row, ref in zip(req[1:], ('main', 'audit_branch', 'baseline_tag')):
        require(set(row) == {'type', 'ref', 'snapshot-id'}
                and row['type'] == 'assert-ref-snapshot-id' and row['ref'] == ref, 'PUBLISH_BODY_INVALID')
        ids.append(sid(row['snapshot-id']))
    update = updates[0]
    require(set(update) == {'action', 'ref-name', 'type', 'snapshot-id'}
            and update['action'] == 'set-snapshot-ref' and update['ref-name'] == 'main'
            and update['type'] == 'branch' and sid(update['snapshot-id']) == ids[1]
            and len(set(ids)) == 3, 'PUBLISH_BODY_INVALID')
    return deepcopy(body)


def verify_transition(before, after, body):
    target = body['updates'][0]['snapshot-id']
    unchanged = ('table_uuid', 'table_location', 'marker', 'schema_sha256', 'structure_sha256',
                 'snapshots', 'last_sequence_number', 'publish_stable_sha256')
    require(all(B.same(before[k], after[k]) for k in unchanged), 'PUBLISH_CHANGED_IMMUTABLE_METADATA')
    require(after['refs'] == {**before['refs'], 'main': {'type': 'branch', 'snapshot_id': target}}
            and after['snapshot_id'] == target and after['metadata_location'] != before['metadata_location']
            and after['last_updated_ms'] >= before['last_updated_ms'], 'PUBLISH_HEAD_UNVERIFIED')
    require(after['snapshot_log'] == before['snapshot_log'] + [
        {'timestamp-ms': after['last_updated_ms'], 'snapshot-id': target}], 'PUBLISH_SNAPSHOT_LOG_DIFFER')
    require(after['metadata_log'] == before['metadata_log'] + [
        {'timestamp-ms': before['last_updated_ms'], 'metadata-file': before['metadata_location']}],
        'PUBLISH_METADATA_LOG_DIFFER')
    return deepcopy(after)


class PublishAdmin(B.BranchAdmin):
    """Existing absent-ref API plus one fixed main CAS; no generic catalog write."""
    def commit_publish(self, body):
        validate_body(body, self.run_token)
        return self._exchange('POST', self.p['table'], body, statuses=(200,))
