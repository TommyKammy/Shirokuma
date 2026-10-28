"""Authenticated HTTP-only managed Mart entry point."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from dataops_client import Client, ClientError, configured_profile, saved_context


class MartClient(Client):
    def request(self, method, path, *, body=None, params=None, key=None, binary=False):
        if params:
            path += '?' + urlencode(params)
        headers = dict(self.headers)
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if key is not None:
            headers['X-Idempotency-Key'] = key
        try:
            with self.opener.open(Request(self.base + path, data=raw, headers=headers, method=method), timeout=3600) as response:
                raw = response.read()
                if response.headers.get('Content-Length') is not None and len(raw) != int(response.headers['Content-Length']):
                    raise ValueError('incomplete response')
                return json.loads(raw)
        except (HTTPError, URLError, OSError):
            # Error bodies are never protected stdout, including grant loss.
            raise ClientError('MART_REQUEST_FAILED') from None

    def checked(self, reply):
        if not isinstance(reply, dict) or reply.get('context') != self.context:
            raise ValueError('response context mismatch')
        return reply

    def get(self, path, **params):
        return self.checked(super().get(path, **params))

    def post(self, path, body, key=None):
        return self.checked(super().post(path, body, key))


def json_file(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError('invalid JSON file') from None


def ref(value, database):
    return (isinstance(value, dict) and set(value) == {'database_id', 'schema_id', 'object_id', 'incarnation'} and
            value['database_id'] == database and all(isinstance(value[k], str) and value[k].strip() for k in ('database_id', 'schema_id', 'object_id')) and
            type(value['incarnation']) is int and value['incarnation'] > 0)


def definition_value(value, context):
    if (not isinstance(value, dict) or set(value) != {'name', 'model_id', 'version', 'source_refs', 'target'} or
            any(not isinstance(value[k], str) or not value[k].strip() for k in ('name', 'model_id')) or
            type(value['version']) is not int or value['version'] < 1 or not isinstance(value['source_refs'], list)):
        raise ValueError('explicit immutable model definition required')
    database = context['database_id']
    sources = value['source_refs']
    if any(not ref(r, database) for r in sources) or len({json.dumps(r, sort_keys=True) for r in sources}) != len(sources):
        raise ValueError('deduplicated same-database watches required')
    target = value['target']
    locator = (isinstance(target, dict) and set(target) == {'database_id', 'schema_id', 'physical_name'} and
               all(isinstance(v, str) and v.strip() for v in target.values()) and '.' not in target['physical_name'])
    if not (ref(target, database) or locator) or target['database_id'] != database or target['schema_id'] != context['schema_id']:
        raise ValueError('explicit MARTS target required')
    return value


def valid_mart(mart, context, owner, mart_id=None):
    return (isinstance(mart, dict) and isinstance(mart.get('id'), str) and bool(mart['id']) and
            (mart_id is None or mart['id'] == mart_id) and mart.get('database_id') == context['database_id'] and
            mart.get('owner') == owner and type(mart.get('revision')) is int and mart['revision'] > 0 and
            isinstance(mart.get('definition'), dict) and mart['definition'].get('target', {}).get('schema_id') == context['schema_id'] and
            all(mart.get('context', {}).get(k) == context[k] for k in ('database_id', 'schema_id', 'warehouse_id', 'sql_catalog', 'sql_schema', 'selected_role')))


def valid_run(run, context, owner, mart_id, run_id=None):
    return (isinstance(run, dict) and isinstance(run.get('id'), str) and bool(run['id']) and
            (run_id is None or run['id'] == run_id) and run.get('mart_id') == mart_id and
            run.get('context') == context and run.get('owner') == owner and
            type(run.get('mart_revision')) is int and run['mart_revision'] > 0 and
            run.get('kind') in ('check', 'refresh') and isinstance(run.get('status'), str) and bool(run['status']))


def parser():
    root = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = root.add_subparsers(dest='action', required=True)
    for action in ('save', 'list', 'get', 'check', 'refresh', 'runs', 'inspect'):
        p = commands.add_parser(action, allow_abbrev=False)
        for name in ('database', 'schema', 'context-file'):
            p.add_argument('--' + name)
        p.add_argument('--api-url', default='http://127.0.0.1:8080')
        if action != 'list':
            p.add_argument('--mart-id', required=action != 'save')
        if action in ('save', 'check', 'refresh'):
            p.add_argument('--expected-revision', type=int, required=True)
            p.add_argument('--idempotency-key', required=True)
        if action == 'save':
            p.add_argument('--definition', required=True)
        if action == 'inspect':
            p.add_argument('--run-id', required=True)
        if action == 'refresh':
            p.add_argument('--check-changes', nargs='?', const=True, default=False,
                           type=lambda v: {'true': True, 'false': False}[v.lower()])
    return root


def main(argv=None, *, profile=None):
    args = parser().parse_args(argv)
    for name, value in vars(args).items():
        if isinstance(value, str) and not value.strip():
            raise ValueError('nonempty flags required')
    if args.context_file and (args.database is not None or args.schema is not None):
        raise ValueError('context-file conflicts with database/schema')
    if not args.context_file and (not args.database or not args.schema):
        raise ValueError('explicit database/schema or context-file required')
    action = args.action
    mart_id = getattr(args, 'mart_id', None)
    revision = getattr(args, 'expected_revision', None)
    if revision is not None and (revision < 0 or action != 'save' and revision < 1 or action == 'save' and bool(mart_id) != (revision > 0)):
        raise ValueError('revision 0 creates; positive revision and mart-id otherwise')
    selected = profile or configured_profile()
    if selected is None:
        raise ValueError('configured private profile required')
    client = MartClient(selected, args.api_url)
    if args.context_file:
        client.context = saved_context(json_file(args.context_file))
        client.role = client.context['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
    else:
        client.select(args.database, args.schema)
    context = saved_context(client.context)
    if client.role != 'admin' or context['sql_schema'].lower() != 'marts':
        raise ValueError('admin MARTS context required')
    session = client.request('GET', '/api/session')
    owner = session.get('username')
    if session.get('authenticated') is not True or not isinstance(owner, str) or not owner:
        raise ValueError('authenticated owner required')
    path = '/api/v1/marts' + ('/' + quote(mart_id, safe='') if mart_id else '')
    body = {'expected_revision': revision}
    if action == 'save':
        definition = definition_value(json_file(args.definition), context)
        model_reply = client.get('/api/v1/wap/models', model_id=definition['model_id'], version=definition['version'])
        models = [model_reply['model']] if 'model' in model_reply else model_reply.get('items')
        if not isinstance(models, list) or len(models) != 1:
            raise ValueError('explicit immutable model required')
        model = models[0]
        if model.get('database_id') != context['database_id'] or model.get('model_id') != definition['model_id'] or model.get('version') != definition['version']:
            raise ValueError('model association mismatch')
        watches = {json.dumps(r, sort_keys=True) for r in model.get('sources', {}).values()}
        if definition['source_refs'] and {json.dumps(r, sort_keys=True) for r in definition['source_refs']} != watches:
            raise ValueError('all explicit model sources required')
        target = model.get('current_target_ref') or model.get('target')
        if mart_id:
            current = client.get(path).get('mart')
            if not valid_mart(current, context, owner, mart_id):
                raise ValueError('Mart association mismatch')
            target = current.get('current_target_ref') or target
        if definition['target'] != target:
            raise ValueError('explicit model/current target required')
        body['definition'] = definition
    if action == 'refresh':
        body['check_changes'] = args.check_changes
    if action in ('check', 'refresh'):
        path += '/checks' if action == 'check' else '/refreshes'
    elif action in ('runs', 'inspect'):
        path += '/runs' + ('/' + quote(args.run_id, safe='') if action == 'inspect' else '')
    reply = (client.post(path, body, args.idempotency_key) if action in ('save', 'check', 'refresh') else client.get(path))
    if action in ('save', 'list', 'get'):
        records = reply.get('items') if action == 'list' else [reply.get('mart')]
        valid = isinstance(records, list) and all(valid_mart(m, context, owner, mart_id) for m in records)
    else:
        records = reply.get('items') if action == 'runs' else [reply.get('run')]
        valid = isinstance(records, list) and all(valid_run(r, context, owner, mart_id, getattr(args, 'run_id', None)) for r in records)
        if valid and action in ('check', 'refresh'):
            valid = records[0]['mart_revision'] == revision and records[0]['kind'] == action
    if not valid:
        raise ValueError('Mart response association mismatch')
    return reply


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        print('Mart request failed; retain the original context and idempotency key.', file=sys.stderr)
        sys.exit(1)
