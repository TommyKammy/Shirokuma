"""HTTP-only metadata audit CLI. No control-store or backend credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote

from dataops_client import Client, configured_profile


def main(argv=None, *, profile=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('list', 'show', 'export', 'telemetry'):
        p = commands.add_parser(action)
        p.add_argument('--api-url', default='http://127.0.0.1:8888')
        p.add_argument('--role', choices=('admin', 'data_engineer', 'bi_viewer'))
        if action != 'telemetry':
            p.add_argument('--scope', choices=('self', 'database', 'platform'), default='self')
            p.add_argument('--database', help='database ID')
        if action in ('show', 'export'):
            p.add_argument('--id', required=True)
        if action == 'export':
            p.add_argument('--output', required=True)
        if action in ('list', 'telemetry'):
            p.add_argument('--limit', type=int, default=100 if action == 'telemetry' else 50)
        if action == 'list':
            for name in ('source', 'state', 'search', 'from', 'to', 'cursor'):
                p.add_argument('--' + name)
    args = parser.parse_args(argv)
    if any(isinstance(v, str) and not v.strip() for v in vars(args).values()):
        raise ValueError('nonempty input required')
    if args.action != 'telemetry' and args.scope != 'platform' and not args.database:
        raise ValueError('explicit database ID required')
    if hasattr(args, 'limit') and not 1 <= args.limit <= 100:
        raise ValueError('limit must be between 1 and 100')
    if hasattr(args, 'id') and not re.fullmatch(r'[A-Za-z0-9_-]{1,2048}', args.id):
        raise ValueError('invalid event ID')
    selected_profile = profile or configured_profile()
    if selected_profile is None:
        raise ValueError('configured client profile required')
    client = Client(selected_profile, args.api_url)
    if args.role:
        client.headers['X-Shirokuma-Role'] = args.role
    params = {}
    if args.action != 'telemetry':
        params['scope'] = args.scope
        if args.database:
            params['database_id'] = args.database
    for key in ('source', 'state', 'search', 'from', 'to', 'limit', 'cursor'):
        value = getattr(args, key, None)
        if value is not None:
            params[key] = value
    path = '/api/v1/audit/' + ('telemetry' if args.action == 'telemetry' else 'events')
    if args.action in ('show', 'export'):
        path += '/' + quote(args.id, safe='')
    if args.action == 'export':
        path += '/download'
        # Fail before HTTP for an existing destination. O_EXCL also closes the race.
        if Path(args.output).exists():
            raise ValueError('output already exists')
        raw = client.request('GET', path, params=params, binary=True)
        json.loads(raw)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
        except BaseException:
            Path(args.output).unlink(missing_ok=True)
            raise
        return dict(file=args.output, metadata_only=True)
    return client.request('GET', path, params=params)


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        print('Audit HTTP request failed (details withheld).', file=sys.stderr)
        sys.exit(1)
