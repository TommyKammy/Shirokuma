"""HTTP-only registered LOCAL runtime client."""
import argparse
import json
import re
import sys
from dataops_client import Client, configured_profile

BASE = '/api/v1/warehouses/LOCAL/runtime'


def main(argv=None, *, profile=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('status','wake','chill','autochill','operation','reconcile'):
        p = commands.add_parser(action)
        p.add_argument('--api-url', default='http://127.0.0.1:8888')
        p.add_argument('--role', choices=('admin','data_engineer','bi_viewer'))
        if action not in ('status','operation'):
            p.add_argument('--key', required=True)
        if action == 'operation':
            p.add_argument('--id', required=True)
        if action == 'autochill':
            p.add_argument('--minutes', type=int, required=True)
            p.add_argument('--expected-revision', type=int, required=True)
    args = parser.parse_args(argv)
    for field in ('key','id'):
        value = getattr(args, field, None)
        if value is not None and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
            raise ValueError('invalid identifier')
    if args.action == 'autochill' and (not 0 <= args.minutes <= 1440 or args.expected_revision < 0):
        raise ValueError('invalid policy')
    selected = profile or configured_profile()
    if selected is None:
        raise ValueError('configured client profile required')
    client = Client(selected, args.api_url)
    if args.role:
        client.headers['X-Shirokuma-Role'] = args.role
    if args.action == 'status':
        return client.request('GET', BASE)
    if args.action == 'operation':
        return client.request('GET', BASE + '/operations/' + args.id)
    action = 'auto-chill' if args.action == 'autochill' else args.action
    body = dict(minutes=args.minutes, expected_revision=args.expected_revision) if action == 'auto-chill' else {}
    return client.request('POST', BASE + '/' + action, body=body, key=args.key)


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except Exception:
        print('Warehouse runtime HTTP request failed (details withheld).', file=sys.stderr)
        sys.exit(1)
