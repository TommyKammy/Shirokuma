"""Ordinary grant management CLI: authenticated HTTP, with no operator material."""
import argparse
import json
from urllib.parse import quote

from dataops_client import Client, configured_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('list', 'set', 'revoke', 'operation'))
    parser.add_argument('--database-id')
    parser.add_argument('--principal-id')
    parser.add_argument('--maximum-role', choices=('bi_viewer', 'data_engineer', 'admin'))
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--idempotency-key')
    parser.add_argument('--operation-id')
    parser.add_argument('--api-url', default='http://127.0.0.1:8888')
    args = parser.parse_args(argv)
    if args.action == 'operation':
        if not args.operation_id:
            raise ValueError('operation-id required')
    elif not args.database_id:
        raise ValueError('database-id required')
    if args.action in ('set', 'revoke'):
        if (not args.principal_id or args.expected_revision is None or args.expected_revision < 0 or
                not args.idempotency_key or args.action == 'set' and not args.maximum_role):
            raise ValueError('principal, desired revision, role and key required')
    profile = configured_profile()
    if profile is None:
        raise ValueError('explicit database client profile required')
    client = Client(profile, args.api_url)
    if args.action == 'operation':
        return client.request('GET', '/api/v1/operations/' + quote(args.operation_id, safe=''))
    route = '/api/v1/databases/' + quote(args.database_id, safe='') + '/grants'
    if args.action == 'list':
        return client.request('GET', route)
    return client.request('POST', route, key=args.idempotency_key, body=dict(
        principal_id=args.principal_id, maximum_role='revoked' if args.action == 'revoke' else args.maximum_role,
        expected_revision=args.expected_revision))


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception:
        raise SystemExit('Grant HTTP request failed (details withheld; no automatic retry)')
