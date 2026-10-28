"""Display metadata CLI using ordinary authenticated HTTP client profiles."""
import argparse
import json
from urllib.parse import quote

from dataops_client import Client, configured_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('list', 'set', 'schema', 'operation'))
    for name in ('database-id', 'schema-id', 'display-name', 'description', 'idempotency-key', 'operation-id'):
        parser.add_argument('--' + name)
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--api-url', default='http://127.0.0.1:8888')
    args = parser.parse_args(argv)
    body = None
    if args.action == 'list':
        route = '/api/v1/admin/databases'
    elif args.action == 'operation':
        if not args.operation_id:
            raise ValueError('operation-id required')
        route = '/api/v1/operations/' + quote(args.operation_id, safe='')
    else:
        identifier = args.database_id if args.action == 'set' else args.schema_id
        if (not identifier or args.display_name is None or args.expected_revision is None
                or args.expected_revision < 1 or not args.idempotency_key or not args.idempotency_key.strip()
                or args.action == 'set' and args.description is None):
            raise ValueError('resource, display metadata, positive revision and key required')
        route = '/api/v1/' + ('databases' if args.action == 'set' else 'schemas') + '/' + quote(identifier, safe='') + '/display'
        body = dict(display_name=args.display_name, expected_revision=args.expected_revision)
        if args.action == 'set':
            body['description'] = args.description
    profile = configured_profile()
    if profile is None:
        raise ValueError('explicit database client profile required')
    client = Client(profile, args.api_url)
    if body is None:
        return client.request('GET', route)
    return client.request('PATCH', route, body=body, key=args.idempotency_key)


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception:
        raise SystemExit('Display HTTP request failed (details withheld; no automatic retry)')
