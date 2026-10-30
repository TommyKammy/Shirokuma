"""HTTP-only compiled SQL to WAP client. Mutations return durable receipts."""
import argparse
import base64
import http.client
import json
from pathlib import Path
import re
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from dataops_client import Client, configured_profile, saved_context


class RequestUncertain(Exception):
    pass


class RequestRejected(ValueError):
    pass


class DbtClient(Client):
    def __init__(self, profile, api_url='http://127.0.0.1:8888'):
        super().__init__(profile, api_url)
        # Use the saved identity actually sent for Basic authentication. Native
        # API targets do not expose Workbench's /api/session endpoint.
        self.principal = base64.b64decode(self.headers['Authorization'].split(' ', 1)[1]).decode().split(':', 1)[0]

    def request(self, method, path, *, body=None, params=None, key=None, binary=False):
        headers = dict(self.headers)
        if params:
            path += '?' + urlencode(params)
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if key is not None:
            headers['Idempotency-Key'] = key
        try:
            with self.opener.open(Request(self.base + path, data=raw, headers=headers, method=method), timeout=60) as response:
                raw = response.read()
                if response.headers.get('Content-Length') is not None and len(raw) != int(response.headers['Content-Length']):
                    raise ValueError('incomplete response')
                return json.loads(raw)
        except HTTPError as error:
            if method == 'POST' and body and 'expected_target' in body:
                try:
                    payload = json.loads(error.read())
                    code = payload.get('error', payload.get('code')) if isinstance(payload, dict) else None
                    code = code.get('code') if isinstance(code, dict) else code
                except (ValueError, OSError):
                    code = None
                if error.code == 409 and code == 'DBT_PUBLICATION_REVIEW_CHANGED':
                    raise RequestRejected(code) from None
                raise RequestUncertain() from None
            if method == 'POST' and error.code >= 500:
                raise RequestUncertain() from None
            raise RequestRejected('DBT_REQUEST_FAILED') from None
        except (URLError, OSError, http.client.HTTPException, ValueError):
            if method == 'POST':
                raise RequestUncertain() from None
            raise ValueError('DBT_REQUEST_FAILED') from None

    def checked(self, reply):
        if not isinstance(reply, dict) or reply.get('context') != self.context:
            raise ValueError('response context mismatch')
        return reply

    def get(self, path, **params):
        return self.checked(super().get(path, **params))

    def post(self, path, body, key=None):
        return self.checked(super().post(path, body, key))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
        raise ValueError('invalid artifact ID')
    return value


def artifact(value, context, kind, principal, expected=None, *, observe=False):
    if not isinstance(value, dict) or value.get('kind') != kind:
        raise ValueError('artifact kind mismatch')
    identifier(value.get('id'))
    if expected is not None and value['id'] != expected:
        raise ValueError('artifact ID mismatch')
    saved = saved_context(value.get('context'))
    # Admin GETs and reconciliation may return an engineer's saved parent.
    admin_observation = context['selected_role'] == 'admin' and observe
    if any(saved[k] != context[k] for k in ('database_id', 'schema_id', 'warehouse_id', 'sql_catalog', 'sql_schema', 'generation_id', 'binding_revision')):
        raise ValueError('artifact database/context mismatch')
    if not admin_observation and saved != context:
        raise ValueError('artifact context mismatch')
    if not isinstance(value.get('actor'), str) or not value['actor'] or not isinstance(value.get('state'), str):
        raise ValueError('invalid artifact metadata')
    if not admin_observation and value['actor'] != principal:
        raise ValueError('artifact owner mismatch')
    def same_database(item):
        if isinstance(item, dict):
            if 'database_id' in item and item['database_id'] != context['database_id']:
                raise ValueError('artifact reference database mismatch')
            for child in item.values():
                same_database(child)
        elif isinstance(item, list):
            for child in item:
                same_database(child)
    same_database(value)
    return value


def validate_reply(reply, context, kind, expected, args, principal):
    action = args.action
    if action == 'publish-preview':
        publication_preview(reply, context, args.run_id, args.node_id)
        return reply
    if action == 'projects':
        if not isinstance(reply.get('projects'), list) or any(not isinstance(p, dict) or not isinstance(p.get('id'), str) for p in reply['projects']):
            raise ValueError('invalid projects')
    else:
        records = reply.get('items') if action in ('plans', 'runs') else [reply.get(kind)]
        if not isinstance(records, list):
            raise ValueError('invalid history')
        for record in records:
            artifact(record, context, kind, principal, expected,
                     observe=action not in ('compile', 'run', 'cancel', 'publish'))
            if action == 'compile' and record.get('project_id') != args.project:
                raise ValueError('project association mismatch')
            if action == 'run' or action == 'runs' and args.plan_id:
                if record.get('plan_id') != args.plan_id:
                    raise ValueError('plan association mismatch')
            if action == 'run':
                if (record.get('executor', 'sql_wap') != args.executor
                        or record.get('selected_node_id') != args.node_id
                        or type(record.get('native_tests', False)) is not bool
                        or record.get('native_tests', False) is not args.native_tests):
                    raise ValueError('executor/node association mismatch')
            if action == 'publish' and (record.get('run_id') != args.run_id or record.get('node_id') != args.node_id):
                raise ValueError('publication association mismatch')
            if action == 'publish' and hasattr(args, 'expected_target') and (record.get('origin') != 'native_table'
                    or json.dumps(record.get('expected_target'), sort_keys=True) != json.dumps(args.expected_target, sort_keys=True) or record.get('parent_run_id') != args.run_id
                    or record.get('plan_id') != args.review_plan_id):
                raise ValueError('native publication review ACK mismatch')
    return reply


def publication_preview(value, context, run_id, node_id):
    """Validate a saved review without refreshing or changing its descriptor."""
    if not isinstance(value, dict):
        raise ValueError('invalid publication preview')
    if 'preview' in value:
        if value.get('context') != context:
            raise ValueError('preview context mismatch')
        value = value['preview']
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
            or value.get('origin') != 'native_table' or value.get('run_id') != run_id
            or value.get('node_id') != node_id or value.get('context') != context):
        raise ValueError('preview association mismatch')
    identifier(value.get('plan_id'))
    d = value.get('expected_target')
    if (not isinstance(d, dict) or set(d) != {'version', 'destination', 'target'}
            or type(d['version']) is not int or d['version'] != 1
            or not isinstance(d['destination'], dict)):
        raise ValueError('invalid reviewed target')
    dest, target = d['destination'], d['target']
    if dest.get('database_id') != context['database_id'] or not isinstance(dest.get('schema_id'), str) or not dest['schema_id']:
        raise ValueError('invalid reviewed destination')
    if 'object_id' in dest:
        if (set(dest) != {'database_id', 'schema_id', 'object_id', 'incarnation'}
                or not isinstance(dest['object_id'], str) or not dest['object_id']
                or type(dest['incarnation']) is not int or dest['incarnation'] < 1
                or not isinstance(target, dict) or set(target) != {'ref', 'snapshot', 'schema_id', 'columns', 'schema_fingerprint'}
                or target.get('ref') != dest or not isinstance(target.get('snapshot'), str)
                or not re.fullmatch(r'[0-9]+', target['snapshot'])
                or not isinstance(target.get('columns'), list) or not target['columns']
                or type(target.get('schema_id')) is not int or target['schema_id'] < 0
                or not isinstance(target.get('schema_fingerprint'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', target['schema_fingerprint'])
                or any(not isinstance(c, dict) or set(c) != {'name', 'type'}
                    or not isinstance(c['name'], str) or not c['name'] or not isinstance(c['type'], (str, dict))
                    for c in target['columns'])):
            raise ValueError('invalid reviewed measurement')
    elif set(dest) != {'database_id', 'schema_id', 'physical_name'} or target is not None or not isinstance(dest.get('physical_name'), str) or not dest['physical_name']:
        raise ValueError('invalid absent destination')
    return value


def parser():
    root = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = root.add_subparsers(dest='action', required=True)
    for action in ('projects', 'compile', 'plans', 'plan', 'run', 'runs', 'status', 'cancel', 'reconcile', 'publish', 'publish-preview', 'publication'):
        p = commands.add_parser(action, allow_abbrev=False)
        p.add_argument('--database')
        p.add_argument('--schema', default=None, help='schema name or ID (default: sandbox)')
        p.add_argument('--api-url', default='http://127.0.0.1:8080')
        group = p.add_mutually_exclusive_group()
        group.add_argument('--context')
        group.add_argument('--context-file')
        if action in ('compile', 'run', 'cancel', 'reconcile', 'publish'):
            p.add_argument('--idempotency-key')
        if action == 'compile':
            p.add_argument('--project', default='lake_marts')
            p.add_argument('--source-schema', default='raw')
            p.add_argument('--target-schema', default='marts')
            p.add_argument('--staging-materialization', choices=('default', 'ephemeral', 'table'), default='default')
        if action in ('plan', 'run', 'runs'):
            p.add_argument('--plan-id', required=action != 'runs')
        if action in ('status', 'cancel', 'reconcile', 'publish', 'publish-preview'):
            p.add_argument('--run-id', required=True)
        if action == 'run':
            p.add_argument('--executor', choices=('sql_wap', 'native_table'), default='sql_wap')
            p.add_argument('--node-id')
            p.add_argument('--native-tests', action='store_true')
        if action in ('publish', 'publish-preview'):
            p.add_argument('--node-id', required=True)
        if action == 'publish':
            p.add_argument('--expected-target-file', help='JSON returned by publish-preview')
        if action == 'publication':
            p.add_argument('--publication-id', required=True)
    return root


def main(argv=None, *, profile=None):
    args = parser().parse_args(argv)
    if any(isinstance(v, str) and not v.strip() for v in vars(args).values()):
        raise ValueError('nonempty flags required')
    if (args.context or args.context_file) and (args.database is not None or args.schema is not None):
        raise ValueError('context conflicts with database/schema')
    if args.action == 'publish-preview' and not (args.context or args.context_file):
        raise ValueError('explicit saved context required for publication review')
    if args.action == 'run':
        if args.native_tests and args.executor != 'native_table':
            raise ValueError('native tests require native executor')
        if args.executor == 'native_table':
            if args.node_id is None or not re.fullmatch(r'[A-Za-z0-9_.-]{1,512}', args.node_id):
                raise ValueError('native node ID required')
        elif args.node_id is not None:
            raise ValueError('node ID requires native executor')
    profile = profile or configured_profile()
    if profile is None:
        raise ValueError('configured private profile required')
    client = DbtClient(profile, args.api_url)
    if args.context or args.context_file:
        raw = args.context or Path(args.context_file).read_text()
        client.context = saved_context(json.loads(raw))
        client.role = client.context['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
    else:
        if not args.database:
            raise ValueError('explicit database or context required')
        client.select(args.database, args.schema or 'sandbox')
    context = saved_context(client.context)
    if client.role not in ('admin', 'data_engineer'):
        raise ValueError('DBT role required')
    action, body, params = args.action, {}, {}
    kind, expected = None, None
    path = '/api/v1/dbt/'
    if action == 'projects':
        path += 'projects'
    elif action in ('compile', 'plans', 'plan'):
        path += 'plans'
        kind = 'plan'
        if action == 'plan':
            expected = identifier(args.plan_id)
            path += '/' + expected
        elif action == 'compile':
            body = dict(project_id=args.project, source_schema=args.source_schema, target_schema=args.target_schema,
                        staging_materialization=None if args.staging_materialization == 'default' else args.staging_materialization)
    elif action == 'publication':
        kind, expected = 'publication', identifier(args.publication_id)
        path += 'publications/' + expected
    else:
        path += 'runs'
        kind = 'run'
        if action == 'run':
            body = dict(plan_id=identifier(args.plan_id))
            if args.executor == 'native_table':
                body.update(executor=args.executor, node_id=args.node_id)
                if args.native_tests:
                    body['native_tests'] = True
        elif action == 'runs':
            if args.plan_id:
                params['plan_id'] = identifier(args.plan_id)
        else:
            expected = identifier(args.run_id)
            path += '/' + expected
            if action in ('cancel', 'reconcile', 'publish', 'publish-preview'):
                path += '/' + ('publication-preview' if action == 'publish-preview' else action)
            if action == 'publish':
                kind, expected = 'publication', None
                body['node_id'] = args.node_id
                if args.expected_target_file:
                    preview = publication_preview(json.loads(Path(args.expected_target_file).read_text()), context, args.run_id, args.node_id)
                    args.review_plan_id = preview['plan_id']
                    args.expected_target = preview['expected_target']
                    body['expected_target'] = args.expected_target
            if action == 'publish-preview':
                params['node_id'] = args.node_id
    mutation = action in ('compile', 'run', 'cancel', 'reconcile', 'publish')
    if mutation:
        key = args.idempotency_key or str(uuid.uuid4())
        try:
            reply = client.post(path, body, key)
            return validate_reply(reply, context, kind, expected, args, client.principal)
        except RequestRejected:
            raise
        except (RequestUncertain, ValueError):
            # A POST may already be accepted even when its response is unusable.
            # Withhold that response and retain only the original safe intent.
            return dict(request_uncertain=True, retry=dict(action=action, method='POST', path=path,
                idempotency_key=key, context=context, intent=body, body=dict(body, context=context)))
    else:
        reply = client.get(path, **params)
    return validate_reply(reply, context, kind, expected, args, client.principal)


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        print('DBT request failed (details withheld). Retain the original context and idempotency key.', file=sys.stderr)
        sys.exit(1)
