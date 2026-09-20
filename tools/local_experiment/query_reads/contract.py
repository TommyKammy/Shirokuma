"""Safe listing of saved Reader metadata; no replay or row-content verification."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re

from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_snapshots import contract as S
from tools.local_experiment.query_filter import contract as F
from tools.local_experiment.query_count import contract as N

PROFILE = 'saved-reader-list-v1'
MAX_RUNS, MAX_SUMMARY_BYTES, MAX_TOTAL_BYTES, MAX_PUBLIC_BYTES = 128, 2 * 1024**2, 32 * 1024**2, 256 * 1024
ROOT_NAMES = {1: 'query-read', 2: 'query-snapshots', 3: 'query-filter', 4: 'query-count'}
ACTIONS = {1: {'tables', 'describe', 'select'}, 2: {'snapshots', 'select'}, 3: {'select'}, 4: {'count'}}
PROFILES = {3: 'filtered-snapshot-reader-v3', 4: 'snapshot-count-reader-v4'}
ERROR_CODES = frozenset('''READS_ROOT_INVALID READS_ROOT_CHANGED READS_ENTRY_LIMIT READS_READ_LIMIT
READS_PUBLIC_LIMIT READS_UNRECOGNIZED_ENTRIES READS_TOKEN_MISSING READS_RECEIPT_MISSING
READS_RECEIPT_INVALID READS_RECEIPT_CHANGED READS_LOCK_MISSING READS_UNFINISHED READS_REQUEST_INVALID'''.split())
LIMITATIONS = [
    'Saved Reader metadata only; no query, row, SQL or model execution is repeated.',
    'Recorded results and cleanup are producer claims checked for local consistency, not current server or table state.',
    'Rows, counts, SQL, predicate literals and columns are omitted.']
TOP_KEYS = frozenset('schema_version command command_result requested_token inventory entries limitations'.split())
ENTRY_KEYS = frozenset('reader_version token action table snapshot_id current_snapshot_id recorded_result cleanup_verified inspection_state error_code started_at evidence'.split())
INVENTORY_KEYS = frozenset('reader_version root complete inspected_entries unrecognized_entries error_code'.split())
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z')
SUMMARY_V1 = frozenset('''catalog cleanup error_code errors evidence_token finished_at identity_after
identity_before identity_unchanged inventory invocations limits observation queries query_drain request
request_sha256 result runtime schema_version source_hashes started_at'''.split())
SUMMARY_NEW = frozenset('''cleanup_verified cloud_calls error_code evidence_token finished_at invocations
limits model_calls observation request request_sha256 result retries schema_version source_hashes started_at'''.split())
V1_OPTIONAL = frozenset('catalog inventory identity_before identity_after identity_unchanged'.split())


class ReadsError(ValueError):
    pass


class PublicLimitError(ReadsError):
    pass


def require(condition):
    if not condition:
        raise ReadsError('READS_RECEIPT_INVALID')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def normalize_request(value):
    require(type(value) is dict and set(value) == {'command', 'token'} and value['command'] == 'reads')
    require(value['token'] is None or (type(value['token']) is str and TOKEN.fullmatch(value['token'])))
    return deepcopy(value)


def parse_request(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 1024)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result); result[key] = value
        return result
    try:
        return normalize_request(json.loads(raw, object_pairs_hook=pairs,
                                 parse_constant=lambda _: require(False)))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ReadsError('READS_RECEIPT_INVALID') from None


def stamp(value):
    require(type(value) is str and STAMP.fullmatch(value))
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
        return date.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError):
        raise ReadsError('READS_RECEIPT_INVALID') from None


def snapshot_id(value):
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,18}', value) and int(value) <= 2**63 - 1)
    return value


def minimal(version, token, state, error=None):
    require(type(version) is int and version in ROOT_NAMES and type(token) is str and TOKEN.fullmatch(token))
    return dict(reader_version=version, token=token, action=None, table=None, snapshot_id=None,
                current_snapshot_id=None, recorded_result=None, cleanup_verified=None,
                inspection_state=state, error_code=error, started_at=None,
                evidence='tmp/local-experiment/' + ROOT_NAMES[version] + '/' + token + '/summary.json')


def _sources(value):
    require(type(value) is dict and 0 < len(value) <= 256)
    for path, digest in value.items():
        require(type(path) is str and len(path) <= 512 and not PurePosixPath(path).is_absolute()
                and '..' not in PurePosixPath(path).parts and type(digest) is str and SHA.fullmatch(digest))


def _rowless(selection, request, identity, version):
    keys = (R.RESULT_KEYS if version == 1 else S.RESULT_KEYS if version == 2 else F.RESULT_KEYS) - {'rows'}
    require(type(selection) is dict and set(selection) == keys
            and type(selection['schema_version']) is int and selection['schema_version'] == version)
    require(selection['table'] == 'iceberg.' + request['table'] and selection['table_uuid'] == identity['table_uuid']
            and selection['schema_sha256'] == identity['schema_sha256'])
    columns = R._columns(selection['columns'])
    require([x['name'] for x in columns] == request['columns']
            and all(column in identity['columns'] for column in columns))
    limit, count, fetched = selection['requested_limit'], selection['row_count'], selection['fetched_row_count']
    require(type(limit) is int and limit == request['limit'] and type(count) is int and 0 <= count <= limit
            and type(fetched) is int and fetched in (count, limit + 1) and count == min(fetched, limit)
            and type(selection['truncated']) is bool and selection['truncated'] == (fetched > limit)
            and selection['ordering'] == 'unspecified' and type(selection['rows_sha256']) is str
            and SHA.fullmatch(selection['rows_sha256']))
    return snapshot_id(selection['snapshot_id'])


def _success(version, saved, request):
    if version == 1:
        observation = saved['observation']
        require(type(observation) is dict and type(observation.get('schema_version')) is int and observation['schema_version'] == 1)
        if request['command'] == 'tables':
            require(set(observation) == {'schema_version','catalog','schema','tables','complete'}
                    and observation['catalog'] == 'iceberg' and observation['schema'] == request['schema']
                    and type(observation['complete']) is bool and type(observation['tables']) is list
                    and len(observation['tables']) <= R.MAX_TABLES)
            for table in observation['tables']:
                require(R._table(table)[0] == request['schema'])
            require(observation['tables'] == sorted(set(observation['tables'])) and observation == saved['inventory'])
            return None, None
        identity = saved['identity_before']
        R.validate_identity(identity, request, identity['columns'])
        R.verify_identity(identity, saved['identity_after'])
        require(saved['identity_unchanged'] is True)
        current = identity['current_snapshot_id']
        if current is not None:
            snapshot_id(current)
        if request['command'] == 'describe':
            require(observation == identity)
            return None, current
        selected = _rowless(observation, request, identity, version)
        require(selected == current)
        return selected, current
    adapter = {2: S, 3: F, 4: N}[version]
    observation = saved['observation']
    require(type(observation) is dict and observation['state'] == 'verified'
            and observation['cleanup_verified'] is True and observation['request_sha256'] == saved['request_sha256'])
    window = adapter.validate_window({'schema_version':2, 'identity': observation['identity_before'], 'snapshots': observation['snapshot_catalog_before']}, request)
    after = adapter.validate_window({'schema_version':2, 'identity': observation['identity_after'], 'snapshots': observation['snapshot_catalog_after']}, request)
    require(window == after)
    identity, selection = window['identity'], observation['selection']
    current = identity['current_snapshot_id']
    if current is not None:
        snapshot_id(current)
    if request['command'] == 'snapshots':
        require(selection is None)
        return None, current
    if version == 4:
        N.validate_public(selection, request)
        selected = selection['snapshot_id']
        require(selection['table_uuid'] == identity['table_uuid'] and selection['schema_sha256'] == identity['schema_sha256'])
    else:
        selected = _rowless(selection, request, identity, version)
    require(selection['current_snapshot_id'] == current
            and selection['selected_snapshot'] in window['snapshots']
            and selection['selected_snapshot']['snapshot_id'] == selected
            and selection['selected_snapshot']['schema_compatibility'] == 'compatible'
            and selected == (request.get('snapshot_id') or current))
    if version == 3:
        predicate = F.resolve_predicate(request, identity['columns'])
        require(selection['where'] == {'column': predicate['column'], 'type': predicate['type'], 'operator': 'eq'})
    return selected, current


def project_run(version, token, saved=None, read_state='stable', error_code=None):
    if read_state != 'stable':
        return minimal(version, token, read_state, error_code)
    entry = minimal(version, token, 'invalid', 'READS_RECEIPT_INVALID')
    try:
        expected = SUMMARY_V1 if version == 1 else SUMMARY_NEW | ({'profile'} if version in PROFILES else set())
        require(type(saved) is dict and (set(saved) == expected if version != 1 else
                SUMMARY_V1 - V1_OPTIONAL <= set(saved) <= SUMMARY_V1) and type(saved['schema_version']) is int
                and saved['schema_version'] == version and saved['evidence_token'] == token
                and type(saved['invocations']) is int and saved['invocations'] in (0,1))
        if version in PROFILES:
            require(saved['profile'] == PROFILES[version])
        if version > 1:
            require(all(type(saved[key]) is int and saved[key] == 0 for key in ('cloud_calls', 'model_calls', 'retries')))
        _sources(saved['source_hashes'])
        request = {1: R, 2: S, 3: F, 4: N}[version].normalize_request(saved['request'])
        require(request['command'] in ACTIONS[version] and saved['request_sha256'] == R.digest(request))
        recorded = saved['result']
        require(recorded in ('running', 'passed', 'failed', 'interrupted'))
        if version == 1:
            require(type(saved['cleanup']) is dict and set(saved['cleanup']) == {'verified'})
            cleanup = saved['cleanup']['verified']
        else:
            cleanup = saved['cleanup_verified']
        require(type(cleanup) is bool)
        started = stamp(saved['started_at'])
        entry.update(action=request['command'], table='iceberg.' + request['table'] if 'table' in request else None,
                     started_at=started, recorded_result=recorded, cleanup_verified=cleanup)
        if recorded == 'running' or saved['finished_at'] is None:
            entry.update(inspection_state='incomplete', error_code='READS_UNFINISHED')
            return entry
        finished = stamp(saved['finished_at'])
        require(finished >= started)
        if recorded == 'passed':
            require(saved['error_code'] is None and cleanup is True and saved['invocations'] == 1)
            entry['snapshot_id'], entry['current_snapshot_id'] = _success(version, saved, request)
        else:
            require(type(saved['error_code']) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', saved['error_code']))
            if recorded == 'interrupted':
                require(saved['error_code'] == 'INTERRUPTED')
        entry.update(inspection_state='saved', error_code=None)
        return entry
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        return minimal(version, token, 'invalid', 'READS_RECEIPT_INVALID')


def inventory(version, root='not-observed', complete=False, error='READS_ROOT_INVALID'):
    return dict(reader_version=version, root=root, complete=complete, inspected_entries=0,
                unrecognized_entries=0, error_code=error)


def _entry(value):
    require(type(value) is dict and set(value) == ENTRY_KEYS)
    version, token, state = value['reader_version'], value['token'], value['inspection_state']
    base = minimal(version, token, state, value['error_code'])
    require(value['evidence'] == base['evidence'] and value['error_code'] in ERROR_CODES | {None})
    require(state in ('saved', 'active', 'incomplete', 'invalid', 'missing'))
    if state in ('active', 'invalid', 'missing') or (state == 'incomplete' and value['action'] is None):
        require(value == base and (value['error_code'] is None if state == 'active' else value['error_code'] is not None))
        return
    require(value['action'] in ACTIONS[version] and type(value['cleanup_verified']) is bool
            and value['recorded_result'] in ('running', 'passed', 'failed', 'interrupted')
            and stamp(value['started_at']) == value['started_at'])
    if value['action'] == 'tables':
        require(value['table'] is None)
    else:
        require(type(value['table']) is str and value['table'].startswith('iceberg.'))
        R.normalize_request({'command': 'describe', 'table': value['table'][8:]})
    if state == 'incomplete':
        require(value['error_code'] == 'READS_UNFINISHED' and value['snapshot_id'] is None and value['current_snapshot_id'] is None)
        return
    require(value['error_code'] is None and value['recorded_result'] != 'running')
    if value['recorded_result'] != 'passed':
        require(value['snapshot_id'] is None and value['current_snapshot_id'] is None)
        return
    require(value['cleanup_verified'] is True)
    if value['action'] in ('select', 'count'):
        snapshot_id(value['snapshot_id'])
        snapshot_id(value['current_snapshot_id'])
    else:
        require(value['snapshot_id'] is None)
    if value['current_snapshot_id'] is not None:
        snapshot_id(value['current_snapshot_id'])
    if value['action'] == 'tables':
        require(value['current_snapshot_id'] is None)
    if version == 1 and value['action'] == 'select':
        require(value['snapshot_id'] == value['current_snapshot_id'])


def _complete(entries, requested_token, roots):
    return (all(row['complete'] and row['error_code'] is None for row in roots)
            and all(row['inspection_state'] in ('saved', 'active') for row in entries)
            and (requested_token is None or bool(entries)))


def build_report(entries, requested_token, roots):
    result = {'schema_version': 1, 'command': 'query reads',
              'command_result': 'complete' if _complete(entries, requested_token, roots) else 'incomplete',
              'requested_token': requested_token, 'inventory': deepcopy(roots),
              'entries': sorted(deepcopy(entries), key=lambda row: (row['reader_version'], row['token'])),
              'limitations': list(LIMITATIONS)}
    validate_public(result)
    return result


def validate_public(value):
    require(type(value) is dict and set(value) == TOP_KEYS and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['command'] == 'query reads'
            and value['limitations'] == LIMITATIONS)
    normalize_request({'command': 'reads', 'token': value['requested_token']})
    rows, roots = value['entries'], value['inventory']
    require(type(rows) is list and len(rows) <= MAX_RUNS and type(roots) is list and len(roots) == 4)
    order = []
    for row in rows:
        _entry(row); order.append((row['reader_version'], row['token']))
        require(value['requested_token'] is None or row['token'] == value['requested_token'])
    require(order == sorted(set(order)))
    for version, root in enumerate(roots, 1):
        require(type(root) is dict and set(root) == INVENTORY_KEYS and type(root['reader_version']) is int
                and root['reader_version'] == version and root['root'] in ('present', 'absent', 'not-observed')
                and type(root['complete']) is bool and root['error_code'] in ERROR_CODES | {None}
                and type(root['inspected_entries']) is int and root['inspected_entries'] == sum(x[0] == version for x in order)
                and type(root['unrecognized_entries']) is int and 0 <= root['unrecognized_entries'] <= 1024)
        require(root['root'] == 'present' or (root['inspected_entries'] == 0 and root['unrecognized_entries'] == 0))
        require(not root['complete'] or (root['error_code'] is None and root['root'] != 'not-observed'
                                       and root['unrecognized_entries'] == 0))
        require(not (root['root'] == 'not-observed' or root['unrecognized_entries'])
                or (not root['complete'] and root['error_code'] is not None))
    expected = 'complete' if _complete(rows, value['requested_token'], roots) else 'incomplete'
    require(value['command_result'] == expected)
    if len(canonical(value)) + 1 > MAX_PUBLIC_BYTES:
        raise PublicLimitError('READS_PUBLIC_LIMIT')
    return deepcopy(value)
