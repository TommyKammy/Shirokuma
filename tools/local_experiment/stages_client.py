"""Authenticated HTTP-only entry point for database Stages."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlencode
from urllib.request import Request
from urllib.error import HTTPError, URLError

from dataops_client import Client, ClientError, configured_profile, saved_context, _write_export


ACTIONS = ('save', 'list', 'get', 'files', 'preview', 'import', 'export', 'runs',
           'inspect', 'cancel', 'exports', 'receipt', 'download',
           'scan', 'scans', 'scan-inspect', 'scan-retry')
MUTATIONS = ('save', 'preview', 'import', 'export', 'cancel', 'scan', 'scan-retry')


class StageError(ClientError):
    def __init__(self, code, reply=None):
        super().__init__(code)
        self.reply = reply


class StageClient(Client):
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
            code, recovery = 'HTTP_REQUEST_FAILED', None
            try:
                payload = json.loads(error.read())
                candidate = payload.get('code')
                if isinstance(candidate, str) and candidate.replace('_', '').isalnum():
                    code = candidate
                details = payload.get('details', {})
                if details.get('context') == self.context and isinstance(details.get('run'), dict):
                    run = details['run']
                    # Preserve only public recovery identity, never arbitrary error bodies.
                    if run.get('context') == self.context and isinstance(run.get('id'), str):
                        fields = ('id', 'stage_id', 'stage_revision', 'kind', 'owner', 'context', 'status',
                                  'operation_id', 'committed', 'export_id', 'publication_state')
                        recovery = dict(context=self.context, run={k: run[k] for k in fields if k in run})
            except (ValueError, TypeError, AttributeError):
                pass
            raise StageError(code, recovery) from None
        except (URLError, OSError):
            raise StageError('HTTP_REQUEST_FAILED') from None

    def checked(self, reply):
        if not isinstance(reply, dict) or reply.get('context') != self.context:
            raise ValueError('response context mismatch')
        return reply

    def get(self, path, **params):
        return self.checked(super().get(path, **params))

    def post(self, path, body, key=None):
        return self.checked(super().post(path, body, key))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='action', required=True)
    for action in ACTIONS:
        p = commands.add_parser(action)
        for flag in ('database', 'schema', 'context-file'):
            p.add_argument('--' + flag)
        p.add_argument('--api-url', default='http://127.0.0.1:8080')
        if action != 'list':
            p.add_argument('--stage-id', required=action != 'save')
        if action in MUTATIONS:
            p.add_argument('--idempotency-key', required=True)
        if action in ('save', 'preview', 'import', 'export', 'scan', 'scan-retry'):
            p.add_argument('--expected-revision', type=int, required=True)
        if action in ('scan-inspect', 'scan-retry'):
            p.add_argument('--scan-id', required=True)
        if action == 'scan-retry':
            p.add_argument('--item-id', required=True)
            p.add_argument('--expected-attempt', type=int, required=True)
        if action == 'save':
            p.add_argument('--definition', required=True)
        if action in ('preview', 'import', 'export'):
            p.add_argument('--file', required=True)
        if action in ('preview', 'import'):
            p.add_argument('--file-version', required=True)
        if action == 'import':
            p.add_argument('--expected-preview-fingerprint', required=True)
        if action == 'export':
            p.add_argument('--source', required=True, help='ResourceRef JSON or @JSON-file')
            p.add_argument('--format', choices=('csv', 'jsonl'), required=True)
        if action in ('inspect', 'cancel'):
            p.add_argument('--run-id', required=True)
        if action in ('receipt', 'download'):
            p.add_argument('--export-id', required=True)
        if action == 'download':
            p.add_argument('--output', required=True)
    return root


def json_file(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError('invalid or unreadable JSON file') from None


def direct_file(value):
    if (not value or value in ('.', '..') or value.startswith('.') or
            '/' in value or '\\' in value or any(ord(c) < 32 for c in value)):
        raise ValueError('direct filename required')
    return value


def definition_value(value):
    fields = {'name', 'directory', 'mode', 'format', 'destination', 'target', 'encoding',
              'delimiter', 'schema_hints', 'key_columns', 'batch_size', 'atomic_append'}
    if (not isinstance(value, dict) or set(value) - fields or
            not isinstance(value.get('name'), str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', value['name']) or
            not isinstance(value.get('directory'), str) or not Path(value['directory']).is_absolute() or
            value.get('mode') not in ('create', 'replace', 'append', 'upsert') or
            value.get('format') not in ('auto', 'csv', 'json', 'jsonl')):
        raise ValueError('invalid Stage definition')
    locator = 'destination' if value['mode'] == 'create' else 'target'
    if not isinstance(value.get(locator), dict) or ('target' if locator == 'destination' else 'destination') in value:
        raise ValueError('explicit destination or stable target required')
    return value


def valid_scan(scan, stage_id, context):
    if (not isinstance(scan, dict) or not isinstance(scan.get('id'), str) or not scan['id'] or
            scan.get('stage_id') != stage_id or scan.get('context') != context or
            type(scan.get('stage_revision')) is not int or scan['stage_revision'] < 1 or
            not isinstance(scan.get('owner'), str) or not scan['owner'] or
            scan.get('status') not in ('queued', 'running', 'succeeded', 'partial', 'failed', 'unknown', 'canceled') or
            type(scan.get('committed')) is not bool or not isinstance(scan.get('items'), list)):
        return False
    ids = set()
    for item in scan['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in ids or
                not isinstance(item.get('file'), str) or not item['file'] or
                not isinstance(item.get('input_fingerprint'), str) or not item['input_fingerprint'] or
                item.get('status') not in ('pending', 'running', 'succeeded', 'skipped', 'failed', 'unknown', 'not_started', 'canceled') or
                type(item.get('attempt')) is not int or item['attempt'] < 0 or
                type(item.get('committed')) is not bool or type(item.get('retryable')) is not bool):
            return False
        ids.add(item['id'])
    return True


def downloadable(receipt, stage_id, export_id, context):
    # Publication and Stage identity accompany the actual P3 integrity metadata.
    if not isinstance(receipt, dict) or not isinstance(receipt.get('file'), str):
        return False
    try:
        direct_file(receipt['file'])
    except (KeyError, TypeError, ValueError):
        return False
    return (isinstance(receipt, dict) and receipt.get('format') in ('csv', 'jsonl') and
            receipt.get('publication_state') == 'published' and receipt.get('stage_id') == stage_id and
            receipt.get('export_id') == export_id and receipt.get('context') == context and
            type(receipt.get('stage_revision')) is int and receipt['stage_revision'] > 0 and
            isinstance(receipt.get('run_id'), str) and bool(receipt['run_id']) and
            type(receipt.get('expected_count')) is int and receipt['expected_count'] >= 0 and
            type(receipt.get('written_count')) is int and receipt['written_count'] == receipt['expected_count'] and
            isinstance(receipt.get('source'), dict) and isinstance(receipt['source'].get('ref'), dict) and
            receipt['source']['ref'].get('database_id') == context['database_id'] and
            type(receipt.get('byte_count')) is int and receipt['byte_count'] >= 0 and
            isinstance(receipt.get('sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', receipt['sha256']) and
            receipt.get('complete', True) is True)


def main(argv=None, *, profile=None):
    args = parser().parse_args(argv)
    action = args.action
    if not args.context_file and (not args.database or not args.schema):
        raise ValueError('explicit database and schema or context-file required')
    context = saved_context(json_file(args.context_file)) if args.context_file else None
    if context and (args.database is not None and args.database not in (context['database_id'], context['sql_catalog']) or
                    args.schema is not None and args.schema not in (context['schema_id'], context['sql_schema'])):
        raise ValueError('saved context does not match explicit selection')
    for name in ('stage_id', 'run_id', 'export_id', 'scan_id', 'item_id', 'idempotency_key', 'file_version', 'expected_preview_fingerprint', 'output'):
        if hasattr(args, name) and getattr(args, name) is not None and not getattr(args, name).strip():
            raise ValueError('explicit nonempty identifier/key required')
    body = {}
    if action == 'scan-retry':
        if args.expected_attempt < 1:
            raise ValueError('positive expected-attempt required')
        body.update(item_id=args.item_id, expected_attempt=args.expected_attempt)
    if hasattr(args, 'expected_revision'):
        revision = args.expected_revision
        if (action == 'save' and (revision < 0 or bool(args.stage_id) != (revision > 0)) or
                action != 'save' and revision < 1):
            raise ValueError('revision 0 creates; positive revision and stage-id required otherwise')
        body['expected_revision'] = revision
    if action == 'save':
        body['definition'] = definition_value(json_file(args.definition))
        if args.stage_id:
            body['stage_id'] = args.stage_id
    if hasattr(args, 'file'):
        body['file'] = direct_file(args.file)
    if action in ('preview', 'import'):
        body['version'] = args.file_version
    if action == 'import':
        body['expected_preview_fingerprint'] = args.expected_preview_fingerprint
    if action == 'export':
        try:
            source = json_file(args.source[1:]) if args.source.startswith('@') else json.loads(args.source)
        except ValueError:
            raise ValueError('source must be ResourceRef JSON or @JSON-file') from None
        if (not isinstance(source, dict) or set(source) != {'database_id', 'schema_id', 'object_id', 'incarnation'} or
                any(not isinstance(source[k], str) or not source[k] for k in ('database_id', 'schema_id', 'object_id')) or
                type(source['incarnation']) is not int or source['incarnation'] < 1):
            raise ValueError('stable ResourceRef required')
        body.update(source=source, format=args.format)
    selected_profile = profile or configured_profile()
    if selected_profile is None:
        raise ValueError('explicit database runtime profile required')
    client = StageClient(selected_profile, args.api_url)
    if context:
        client.context = context
        client.role = context['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
    else:
        client.select(args.database, args.schema)
    if client.role != 'admin':
        raise ValueError('Stage requires admin role')
    if action == 'export' and body['source']['database_id'] != client.context['database_id']:
        raise ValueError('source context mismatch')
    if action == 'save':
        locator = body['definition'].get('target', body['definition'].get('destination'))
        if locator.get('database_id') != client.context['database_id']:
            raise ValueError('target context mismatch')
    base = '/api/v1/stages'
    path = base + ('/' + quote(args.stage_id, safe='') if getattr(args, 'stage_id', None) and action != 'save' else '')
    if action in ('scan', 'scans', 'scan-inspect', 'scan-retry'):
        path += '/scans'
        if action in ('scan-inspect', 'scan-retry'):
            path += '/' + quote(args.scan_id, safe='')
        reply = (client.post(path + ('/retry' if action == 'scan-retry' else ''), body, args.idempotency_key)
                 if action in ('scan', 'scan-retry') else client.get(path))
        records = reply.get('scans') if action == 'scans' else [reply.get('scan')]
        if not isinstance(records, list) or any(not valid_scan(s, args.stage_id, client.context) for s in records):
            raise ValueError('invalid scan response')
        if action == 'scan-inspect' and records[0]['id'] != args.scan_id:
            raise ValueError('scan identity mismatch')
        if action in ('scan', 'scan-retry') and records[0]['stage_revision'] != args.expected_revision:
            raise ValueError('scan revision mismatch')
        if action == 'scan-retry' and (records[0]['id'] == args.scan_id or
                records[0].get('source_scan_id') != args.scan_id or records[0].get('source_item_id') != args.item_id or
                len(records[0]['items']) != 1 or records[0]['items'][0]['attempt'] != args.expected_attempt + 1):
            raise ValueError('retry identity mismatch')
        return reply
    if action in ('list', 'get'):
        return client.get(path)
    if action in ('files', 'runs', 'exports'):
        return client.get(path + '/' + action)
    if action in ('inspect', 'cancel'):
        path += '/runs/' + quote(args.run_id, safe='')
        return client.post(path + '/cancel', {}, args.idempotency_key) if action == 'cancel' else client.get(path)
    if action in ('receipt', 'download'):
        path += '/exports/' + quote(args.export_id, safe='')
        reply = client.get(path)
        if action == 'receipt':
            return reply
        receipt = reply.get('receipt')
        if reply.get('stage_id') != args.stage_id or not downloadable(receipt, args.stage_id, args.export_id, client.context):
            raise ValueError('complete published export required')
        raw = client.request('GET', path + '/content', params={'context': json.dumps(client.context)}, binary=True)
        _write_export(args.output, raw, receipt)
        return dict(reply, file=args.output)
    suffix = {'save': '', 'preview': '/preview', 'import': '/imports', 'export': '/exports'}[action]
    reply = client.post(path + suffix, body, args.idempotency_key)
    if action == 'import' and isinstance(reply.get('run'), dict) and reply['run'].get('id') is None:
        run = reply['run']
        if (set(run) != {'id', 'status', 'committed', 'deduplicated', 'context'} or
                run['id'] is not None or run['status'] != 'skipped' or run['committed'] is not True or
                run['deduplicated'] is not True or run['context'] != client.context):
            raise ValueError('invalid duplicate disposition')
    return reply


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except ClientError as error:
        if error.reply:
            print(json.dumps(error.reply, ensure_ascii=False))
        print('Stage request failed; inspect the original saved context and run.', file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Stage request failed; inspect the original saved context and inputs.', file=sys.stderr)
        sys.exit(1)
