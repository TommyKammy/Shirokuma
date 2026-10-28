"""Normal authenticated HTTP retirement client. No local backend execution."""
import argparse
import json
import re
import sys

from dataops_client import Client, ClientError, configured_profile


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid retirement identifier')
    return value


def positive(value):
    return type(value) is int and value > 0


def database(value, database_id=None, retired=False):
    if (not isinstance(value, dict) or set(value) != {'id', 'sql_catalog', 'state', 'revision'} or
            not positive(value['revision']) or
            any(not isinstance(value[k], str) or not value[k] for k in ('id', 'sql_catalog', 'state')) or
            database_id is not None and value['id'] != database_id or
            retired and value['state'] not in ('retiring', 'retired')):
        raise ValueError('Invalid retirement database response')
    identifier(value['id'])
    return value


def dependencies(rows, database_id, blocker=False):
    base = {'kind', 'id', 'state', 'database_id'} | ({'code'} if blocker else set())
    links = {'job_id', 'stage_id', 'mart_id', 'model_id', 'operation_id', 'run_id', 'scan_id',
             'schedule_id', 'subject_id', 'subject_kind', 'target_id', 'target_kind', 'parent_id'}
    if not isinstance(rows, list):
        raise ValueError('Invalid retirement dependency list')
    for row in rows:
        if isinstance(row, dict) and row.get('kind') == 'resource_ref':
            if (blocker or set(row) != base | {'schema_id', 'object_id', 'incarnation'} or
                    any(not isinstance(row[k], str) or not row[k] for k in base | {'schema_id', 'object_id'}) or
                    row['database_id'] != database_id or row['state'] != 'RETAINED' or
                    row['id'] != row['object_id'] or not positive(row['incarnation'])):
                raise ValueError('Invalid retirement dependency identity')
            continue
        if (not isinstance(row, dict) or not base <= set(row) or
                any(not isinstance(v, str) or not v for v in row.values()) or
                row['database_id'] != database_id or
                set(row) - base - links):
            raise ValueError('Invalid retirement dependency identity')
    return rows


def operation(value, database_id=None, operation_id=None):
    fields = {'id', 'action', 'actor', 'database_id', 'revision', 'state', 'database',
              'dependencies', 'blockers', 'stopped_schedule_ids'}
    if (not isinstance(value, dict) or set(value) != fields or value['action'] != 'DATABASE_RETIRE' or
            value['state'] not in ('RETIRING', 'RETIRED') or not positive(value['revision']) or
            not isinstance(value['actor'], str) or not value['actor'] or
            operation_id is not None and value['id'] != operation_id or
            database_id is not None and value['database_id'] != database_id):
        raise ValueError('Invalid retirement operation response')
    identifier(value['id'])
    identifier(value['database_id'])
    database(value['database'], value['database_id'], retired=True)
    if value['database']['state'] != value['state'].lower():
        raise ValueError('Inconsistent retirement state')
    dependencies(value['dependencies'], value['database_id'])
    dependencies(value['blockers'], value['database_id'], True)
    stopped = value['stopped_schedule_ids']
    if not isinstance(stopped, list) or any(not isinstance(v, str) or not v for v in stopped) or len(set(stopped)) != len(stopped):
        raise ValueError('Invalid stopped schedule identities')
    return value


def preview(value, database_id):
    if not isinstance(value, dict) or set(value) != {'database', 'dependencies', 'blockers', 'stopped_schedule_ids', 'operation'}:
        raise ValueError('Invalid retirement preview response')
    database(value['database'], database_id)
    dependencies(value['dependencies'], database_id)
    dependencies(value['blockers'], database_id, True)
    if value['operation'] is not None:
        operation(value['operation'], database_id)
        if value['operation']['database']['sql_catalog'] != value['database']['sql_catalog']:
            raise ValueError('Preview database identity mismatch')
    expected_stopped = value['operation']['stopped_schedule_ids'] if value['operation'] is not None else []
    if value['stopped_schedule_ids'] != expected_stopped:
        raise ValueError('Preview stopped schedule identities mismatch')
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate response field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number')))


def main(argv=None, *, profile=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('retire-preview', 'retire', 'retire-status', 'retire-reconcile'):
        p = commands.add_parser(action)
        p.add_argument('--base-url', default='http://127.0.0.1:8888')
        p.add_argument('--database-id' if action in ('retire-preview', 'retire') else '--operation-id', required=True)
        if action in ('retire', 'retire-reconcile'):
            p.add_argument('--expected-revision', type=int, required=True)
            p.add_argument('--idempotency-key', required=True)
    args = parser.parse_args(argv)
    dbid = getattr(args, 'database_id', None)
    opid = getattr(args, 'operation_id', None)
    identifier(dbid or opid)
    mutation = args.action in ('retire', 'retire-reconcile')
    key = getattr(args, 'idempotency_key', None)
    if mutation and (not positive(args.expected_revision) or not key.strip() or len(key) > 256 or
                     any(ord(c) < 32 or ord(c) == 127 for c in key)):
        raise ValueError('Positive revision and nonempty idempotency key required')
    try:
        selected = profile or configured_profile()
    except Exception:
        raise ValueError('Configured database client profile unavailable') from None
    if selected is None:
        raise ValueError('Configured database client profile required')
    try:
        client = Client(selected, args.base_url)
    except Exception:
        raise ValueError('Retirement HTTP client configuration invalid') from None
    path = ('/api/v1/admin/databases/' + dbid + '/retire' if dbid else
            '/api/v1/operations/' + opid + ('/reconcile' if mutation else ''))
    try:
        raw = client.request('POST' if mutation else 'GET', path, binary=True,
                             **(dict(body={'expected_revision': args.expected_revision}, key=key) if mutation else {}))
        reply = strict_json(raw)
        return preview(reply, dbid) if args.action == 'retire-preview' else operation(reply, dbid, opid)
    except ClientError as error:
        code = str(error)
        safe = code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', code) else 'HTTP_REQUEST_FAILED'
        raise ValueError(safe + '; inspect retirement status; retain original revision and key') from None
    except Exception:
        raise ValueError('Retirement response unavailable or invalid; outcome may be unknown. Retain original revision and key.') from None


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception as error:
        # Only locally generated validation messages escape this module.
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
