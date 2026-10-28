"""Authenticated HTTP entry point for database Jobs; arguments accepts JSON or @file."""
import argparse
import base64
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

from dataops_client import Client, ClientError, configured_profile, saved_context


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='action', required=True)
    for action in ('list', 'save', 'show', 'run', 'runs', 'inspect', 'cancel', 'reconcile'):
        p = commands.add_parser(action)
        for flag in ('database', 'schema', 'context-file'):
            p.add_argument('--' + flag)
        p.add_argument('--api-url', default='http://127.0.0.1:8888')
        if action in ('save', 'show', 'run', 'runs'):
            p.add_argument('--job-id', required=action in ('show', 'run'))
        if action in ('inspect', 'cancel', 'reconcile'):
            p.add_argument('--run-id', required=True)
        if action in ('save', 'run'):
            p.add_argument('--expected-revision', required=True, type=int)
            p.add_argument('--idempotency-key', required=True)
        if action == 'save':
            p.add_argument('--definition', required=True, help='definition JSON file')
        if action == 'run':
            p.add_argument('--arguments', required=True, help='JSON object, or @path to JSON file')
            p.add_argument('--input', action='append', default=[], help='name=path (repeatable)')
    return root


def json_file(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError('invalid or unreadable JSON file') from None


def main(argv=None, *, profile=None):
    args = parser().parse_args(argv)
    action = args.action
    if not args.context_file and (not args.database or not args.schema):
        raise ValueError('explicit database and schema or context-file required')
    context = saved_context(json_file(args.context_file)) if args.context_file else None
    if context and (args.database is not None and args.database not in (context['database_id'], context['sql_catalog']) or
                    args.schema is not None and args.schema not in (context['schema_id'], context['sql_schema'])):
        raise ValueError('saved context does not match explicit selection')
    for name in ('job_id', 'run_id', 'idempotency_key'):
        if hasattr(args, name) and getattr(args, name) is not None and not getattr(args, name).strip():
            raise ValueError('explicit nonempty identifier/key required')
    body = {}
    if action in ('save', 'run'):
        revision = args.expected_revision
        if (action == 'run' and revision < 1 or action == 'save' and
                (revision < 0 or bool(args.job_id) != (revision > 0))):
            raise ValueError('revision 0 creates; positive revision and job-id required for updates/runs')
        body['expected_revision'] = revision
    if action == 'save':
        definition = json_file(args.definition)
        if not isinstance(definition, dict) or set(definition) - {'name', 'description', 'parameters', 'steps'}:
            raise ValueError('definition requires only name, description, parameters and steps')
        if (not isinstance(definition.get('name'), str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', definition['name']) or
                not isinstance(definition.get('parameters', {}), dict) or
                not isinstance(definition.get('steps'), list) or not 1 <= len(definition['steps']) <= 50):
            raise ValueError('definition name and 1..50 steps required')
        body['definition'] = definition
        if args.job_id:
            body['job_id'] = args.job_id
    if action == 'run':
        try:
            arguments = json_file(args.arguments[1:]) if args.arguments.startswith('@') else json.loads(args.arguments)
        except ValueError:
            raise ValueError('arguments must be a JSON object or @JSON-file') from None
        if not isinstance(arguments, dict):
            raise ValueError('arguments must be a JSON object')
        body['arguments'] = arguments
        inputs = {}
        for entry in args.input:
            name, sep, filename = entry.partition('=')
            if not sep or not name.strip() or not filename or name in inputs:
                raise ValueError('unique input name=path required')
            path = Path(filename)
            try:
                inputs[name] = dict(filename=path.name, content_base64=base64.b64encode(path.read_bytes()).decode())
            except OSError:
                raise ValueError('input file unreadable') from None
        if inputs:
            body['inputs'] = inputs
    selected_profile = profile or configured_profile()
    if selected_profile is None:
        raise ValueError('explicit database runtime profile required')
    client = Client(selected_profile, args.api_url)
    if context:
        client.context = context
        client.role = context['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
    else:
        client.select(args.database, args.schema)
    base = '/api/v1/jobs'
    if action == 'list':
        return client.get(base)
    if action == 'show':
        return client.get(base + '/' + quote(args.job_id, safe=''))
    if action == 'runs':
        return client.get(base + '/runs', **({'job_id': args.job_id} if args.job_id else {}))
    if action == 'inspect':
        return client.get(base + '/runs/' + quote(args.run_id, safe=''))
    if action in ('cancel', 'reconcile'):
        return client.post(base + '/runs/' + quote(args.run_id, safe='') + '/' + action, {})
    path = base if action == 'save' else base + '/' + quote(args.job_id, safe='') + '/runs'
    return client.post(path, body, args.idempotency_key)


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        # Never echo an HTTP body, credentials, file contents, or user arguments.
        print('Job request failed; inspect the saved context and explicit inputs.', file=sys.stderr)
        sys.exit(1)
