"""HTTP-only database CLI. Never opens the control store or a backend adapter."""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

import runtime_profile


class ClientError(ValueError):
    def __init__(self, code, reply=None):
        super().__init__(code)
        self.reply = reply if isinstance(reply, dict) and isinstance(reply.get('context'), dict) and isinstance(reply.get('operation'), dict) else None


def action_label(argv):
    for group in ('wap', 'operation'):
        for index, value in enumerate(argv):
            if value == '--' + group and index + 1 < len(argv):
                return group + '-' + argv[index + 1]
            if value.startswith('--' + group + '='):
                return group + '-' + value.split('=', 1)[1]
    return 'export' if any(v == '--export' or v.startswith('--export=') for v in argv) else 'ingest'


def saved_context(value):
    if isinstance(value, dict) and 'context' in value:
        value = value['context']
    names = {'database_id', 'schema_id', 'warehouse_id', 'sql_catalog', 'sql_schema',
             'selected_role', 'binding_revision', 'generation_id', 'grant_revision'}
    if (not isinstance(value, dict) or set(value) != names or
            any(not isinstance(value[k], str) or not value[k].strip() for k in names - {'binding_revision', 'grant_revision'}) or
            value['selected_role'] not in ('admin', 'data_engineer', 'bi_viewer') or
            type(value['binding_revision']) is not int or value['binding_revision'] < 1 or
            value['grant_revision'] is not None and (type(value['grant_revision']) is not int or value['grant_revision'] < 1)):
        raise ValueError('exact saved resolved context required')
    return dict(value)


def configured_profile():
    """Read profile references only; the CLI must never load backend secrets."""
    explicit = os.environ.get('SHIROKUMA_RUNTIME_PROFILE')
    path = Path(explicit) if explicit is not None else runtime_profile.ROOT / 'var/shirokuma/runtime-profile.json'
    if explicit is None and not path.exists() and not path.with_suffix('.active').exists():
        return None
    value = runtime_profile.private_json(path)
    if value.get('mode') not in ('database', 'logical_v1') or not isinstance(value.get('client_auth_file'), str):
        raise ValueError('invalid database runtime profile')
    return SimpleNamespace(client_auth_file=value['client_auth_file'])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, profile, api_url='http://127.0.0.1:8888'):
        url = urlsplit(api_url)
        try:
            local = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            local = False
        if not local or url.scheme != 'http' or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('api-url must be a local loopback HTTP origin')
        auth = runtime_profile.private_json(profile.client_auth_file)
        if (set(auth) != {'username', 'password', 'selected_role'} or
                auth['selected_role'] not in ('admin', 'data_engineer', 'bi_viewer') or
                any(not isinstance(auth[n], str) or not auth[n] for n in auth)):
            raise ValueError('invalid client credentials')
        self.role = auth['selected_role']
        self.headers = {'Authorization': 'Basic ' + base64.b64encode((auth['username'] + ':' + auth['password']).encode()).decode(),
                        'X-Shirokuma-Role': self.role}
        self.base = api_url.rstrip('/')
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.context = None

    def request(self, method, path, *, body=None, params=None, key=None, binary=False):
        if params:
            path += '?' + urlencode(params)
        headers = dict(self.headers)
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if key is not None:
            headers['Idempotency-Key'] = key
        try:
            with self.opener.open(Request(self.base + path, data=raw, headers=headers, method=method), timeout=3600) as response:
                data = response.read()
                if response.headers.get('Content-Length') is not None and len(data) != int(response.headers['Content-Length']):
                    raise ValueError('incomplete HTTP response')
                return data if binary else json.loads(data)
        except HTTPError as error:
            recovery = None
            try:
                payload = json.loads(error.read())
                code = payload['code']
                if not isinstance(code, str) or not code.replace('_', '').isalnum():
                    raise ValueError()
                recovery = payload.get('details')
            except Exception:
                code = 'HTTP_REQUEST_FAILED'
            raise ClientError(code, recovery) from None
        except (URLError, OSError):
            raise ValueError('HTTP_REQUEST_FAILED') from None

    @staticmethod
    def _select(items, value, name):
        matches = [item for item in items if value in (item['id'], item[name])]
        if len(matches) != 1:
            raise ValueError('explicit unambiguous database/schema required')
        return matches[0]

    def select(self, database, schema):
        db = self._select(self.request('GET', '/api/v1/databases')['items'], database, 'sql_catalog')
        sc = self._select(self.request('GET', '/api/v1/databases/' + quote(db['id'], safe='') + '/schemas')['items'], schema, 'sql_name')
        self.context = dict(database_id=db['id'], schema_id=sc['id'], warehouse_id='LOCAL', sql_catalog=db['sql_catalog'],
            sql_schema=sc['sql_name'], selected_role=self.role, binding_revision=db['binding_revision'],
            generation_id=db['generation_id'], grant_revision=db['grant_revision'])
        return self.context

    def ref(self, name):
        if not isinstance(name, str) or not name or '.' in name:
            raise ValueError('table name must be unqualified; use explicit database and schema')
        result = self.request('GET', '/api/v1/objects/resolve', params=dict(database_id=self.context['database_id'],
            schema_id=self.context['schema_id'], name=name))
        ref = {k: result['ref'][k] for k in ('database_id', 'schema_id', 'object_id', 'incarnation')}
        if ref['database_id'] != self.context['database_id'] or ref['schema_id'] != self.context['schema_id']:
            raise ValueError('resource context mismatch')
        return ref

    def get(self, path, **params):
        return self.request('GET', path, params=dict(context=json.dumps(self.context), **params))

    def post(self, path, body, key=None):
        return self.request('POST', path, body=dict(body, context=self.context), key=key)


def _successful(reply):
    operation = reply['operation']
    if operation['state'] != 'FINISHED':
        raise ClientError('OPERATION_' + operation['state'], reply)
    return operation


def _write_export(path, raw, receipt):
    if len(raw) != receipt['byte_count'] or hashlib.sha256(raw).hexdigest() != receipt['sha256']:
        raise ValueError('EXPORT_INTEGRITY_FAILED')
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None, *, profile=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?')
    parser.add_argument('--database')
    parser.add_argument('--schema')
    parser.add_argument('--api-url', default='http://127.0.0.1:8888')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--table')
    parser.add_argument('--mode', choices=['create', 'replace', 'append', 'upsert'])
    parser.add_argument('--format', default='auto')
    parser.add_argument('--encoding')
    parser.add_argument('--delimiter')
    parser.add_argument('--schema-hints', type=json.loads)
    parser.add_argument('--key', default='')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--atomic-append', action='store_true')
    parser.add_argument('--expected-preview-fingerprint')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--export')
    parser.add_argument('--output')
    parser.add_argument('--max-rows', type=int)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--wap', choices=['save', 'model', 'models', 'build', 'audit', 'publish', 'rollback', 'get-build', 'get-audit', 'get-publication'])
    parser.add_argument('--operation', choices=['get', 'cancel'])
    parser.add_argument('--id')
    parser.add_argument('--model-id')
    parser.add_argument('--version', type=int)
    parser.add_argument('--expected-version', type=json.loads, default=argparse.SUPPRESS)
    parser.add_argument('--sql')
    parser.add_argument('--sources', type=json.loads)
    parser.add_argument('--target', type=json.loads)
    parser.add_argument('--context', type=json.loads, help='original full resolved context for operation followups')
    parser.add_argument('--context-file', help='saved full context or Operation envelope for followups')
    parser.add_argument('--min-rows', type=int, default=1)
    parser.add_argument('--not-null', default='')
    parser.add_argument('--build-id')
    parser.add_argument('--audit-id')
    parser.add_argument('--publication-id')
    args = parser.parse_args(argv)
    if sum(bool(v) for v in (args.export, args.wap, args.operation, args.file)) != 1:
        raise ValueError('one explicit action required')
    submission = bool(args.export or args.file and not args.preview or args.wap in ('build', 'audit', 'publish', 'rollback'))
    if submission and (not args.idempotency_key or not args.idempotency_key.strip()):
        raise ValueError('idempotency-key required')
    if args.max_rows is not None:
        raise ValueError('database exports do not support row limits')
    client = Client(profile or runtime_profile.current(), args.api_url)
    if args.operation:
        if not args.id or bool(args.context_file) == (args.context is not None):
            raise ValueError('operation id and one saved context required')
        c = saved_context(json.loads(Path(args.context_file).read_text()) if args.context_file else args.context)
        if (args.database is not None and args.database not in (c['database_id'], c['sql_catalog']) or
                args.schema is not None and args.schema not in (c['schema_id'], c['sql_schema'])):
            raise ValueError('saved context does not match explicit selection')
        client.role = c['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
        client.context = c
        path = '/api/v1/dataops/' + quote(args.id, safe='')
        if args.operation == 'cancel':
            return client.post(path + '/cancel', {})
        return client.get(path)
    if not args.database or not args.schema:
        raise ValueError('explicit database and schema required')
    client.select(args.database, args.schema)
    if args.wap:
        action = args.wap
        if action in ('model', 'models'):
            params = {k: v for k, v in dict(model_id=args.model_id, version=args.version).items() if v is not None}
            return client.get('/api/v1/wap/models', **params)
        if action.startswith('get-'):
            if not args.id:
                raise ValueError('artifact id required')
            return client.get('/api/v1/wap/' + action[4:] + 's/' + quote(args.id, safe=''))
        if action == 'save':
            if not hasattr(args, 'expected_version'):
                raise ValueError('expected-version required (null creates)')
            body = dict(model_id=args.model_id, sql=args.sql, sources=args.sources, target=args.target,
                expected_version=args.expected_version, min_rows=args.min_rows, not_null=args.not_null.split(',') if args.not_null else [])
            return client.post('/api/v1/wap/models', body)
        names = {'build': ('model_id', 'version'), 'audit': ('build_id',), 'publish': ('build_id', 'audit_id'), 'rollback': ('publication_id',)}[action]
        body = {n: getattr(args, n) for n in names}
        if any(v is None for v in body.values()):
            raise ValueError('explicit WAP identifiers required')
        reply = client.post('/api/v1/wap/' + action, body, args.idempotency_key)
        _successful(reply)
        return reply
    if args.export:
        if not args.output:
            raise ValueError('export output required')
        format = args.format if args.format != 'auto' else {'.csv': 'csv', '.jsonl': 'jsonl', '.ndjson': 'jsonl'}.get(Path(args.output).suffix.lower())
        if format not in ('csv', 'jsonl'):
            raise ValueError('database export supports csv/jsonl only')
        reply = client.post('/api/v1/table-exports', dict(source=client.ref(args.export), format=format), args.idempotency_key)
        operation = _successful(reply)
        failure = 'EXPORT_DOWNLOAD_FAILED'
        try:
            export_id = operation['result']['export_id']
            path = '/api/v1/table-exports/' + quote(export_id, safe='')
            receipt = client.get(path)['receipt']
            raw = client.request('GET', path + '/content', params=dict(context=json.dumps(client.context)), binary=True)
            failure = 'EXPORT_OUTPUT_WRITE_FAILED'
            _write_export(args.output, raw, receipt)
        except Exception as error:
            # The server export is already accepted and complete. Preserve its
            # recovery record even when a later download or local write fails.
            code = str(error) if isinstance(error, ClientError) else failure
            if isinstance(error, ValueError) and str(error) == 'EXPORT_INTEGRITY_FAILED':
                code = 'EXPORT_INTEGRITY_FAILED'
            raise ClientError(code, reply) from None
        return dict(action='export', context=client.context, operation=operation, receipt=receipt, file=args.output)
    if not args.table or not args.mode:
        raise ValueError('explicit table and mode required')
    if args.format not in ('auto', 'csv', 'json', 'jsonl'):
        raise ValueError('unsupported database ingest format')
    path = Path(args.file)
    body = dict(filename=path.name, content_base64=base64.b64encode(path.read_bytes()).decode(), mode=args.mode,
        format=args.format, encoding=args.encoding, delimiter=args.delimiter, schema_hints=args.schema_hints,
        key_columns=args.key.split(',') if args.key else [], batch_size=args.batch_size, atomic_append=args.atomic_append)
    if args.mode == 'create':
        if '.' in args.table:
            raise ValueError('unqualified table required')
        body['destination'] = dict(database_id=client.context['database_id'], schema_id=client.context['schema_id'], physical_name=args.table)
    else:
        body['target'] = client.ref(args.table)
    if args.expected_preview_fingerprint:
        body['expected_preview_fingerprint'] = args.expected_preview_fingerprint
    reply = client.post('/api/v1/ingests' + ('/preview' if args.preview else ''), body, args.idempotency_key)
    if not args.preview:
        _successful(reply)
    return dict(action='ingest', **reply)
