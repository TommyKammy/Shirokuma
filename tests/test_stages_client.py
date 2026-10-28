"""Contract-based offline Stage HTTP entry tests; no backend or local server."""
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import stages_client as stages

C = dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales',
         sql_schema='raw', selected_role='admin', binding_revision=2, generation_id='gen', grant_revision=7)
REF = dict(database_id='db', schema_id='other', object_id='table', incarnation=1)


class StagesClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.context = self.root / 'context.json'
        self.context.write_text(json.dumps({'context': C}))
        self.definition = self.root / 'definition.json'
        self.definition.write_text(json.dumps(dict(name='daily', directory='/approved/input', mode='append', format='csv', target=REF)))
        self.calls = []
        self.raw = b'code\n001\n'
        self.receipt = dict(export_id='e', stage_id='s', stage_revision=2, run_id='r', file='out.csv',
                            publication_state='published', owner='owner', context=C, format='csv', expected_count=1, written_count=1,
                            byte_count=len(self.raw), sha256=hashlib.sha256(self.raw).hexdigest(), source={'snapshot_id': 4, 'ref': REF})
        self.failure = None
        self.reply_context = C
        self.scan_reply = None
        self.run_reply = None

    def call(self, args):
        test = self
        class Response(io.BytesIO):
            headers = {}
        class Opener:
            def open(self, req, **kwargs):
                test.calls.append(req)
                if test.failure:
                    raise HTTPError(req.full_url, 500, '', {}, io.BytesIO(json.dumps(test.failure).encode()))
                path = urlsplit(req.full_url).path
                if path == '/api/v1/databases':
                    reply = {'items': [dict(id='db', sql_catalog='sales', binding_revision=2, generation_id='gen', grant_revision=7)]}
                elif path.endswith('/schemas'):
                    reply = {'items': [dict(id='sc', sql_name='raw')]}
                elif path.endswith('/content'):
                    return Response(test.raw)
                else:
                    reply = dict(context=test.reply_context, stage_id='s', stage={'id': 's'}, run={'id': 'r'}, receipt=test.receipt)
                    if test.scan_reply is not None:
                        reply.update(test.scan_reply)
                    if test.run_reply is not None:
                        reply['run'] = test.run_reply
                return Response(json.dumps(reply).encode())
        with ExitStack() as stack:
            stack.enter_context(patch('dataops_client.runtime_profile.private_json', return_value=dict(username='owner', password='withheld', selected_role='admin')))
            stack.enter_context(patch('dataops_client.build_opener', return_value=Opener()))
            return stages.main(args, profile=SimpleNamespace(client_auth_file='auth'))

    def saved(self, action, *args):
        return [action, '--context-file', str(self.context), *args]

    def test_normal_database_schema_selection_through_native_handler(self):
        from test_stage_routes import StageHTTPTests
        fixture = StageHTTPTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.native = True
        fixture.c['warehouse_id'] = 'LOCAL'
        import control_store
        with control_store.connect() as db:
            db.execute('UPDATE warehouse_config_generations SET warehouse_id=? WHERE id=?',
                       ('LOCAL', fixture.c['generation_id']))
        status, saved = fixture.save()
        self.assertEqual(status, 200, saved)
        stage = saved['stage']
        calls = []
        class Response(io.BytesIO):
            headers = {}
        class Opener:
            def open(self, req, **kwargs):
                calls.append(req)
                url = urlsplit(req.full_url)
                status, reply = fixture.request(url.path + ('?' + url.query if url.query else ''),
                    method=req.method, context=False, request_headers=req.header_items())
                payload = json.dumps(reply).encode()
                if status >= 400:
                    raise HTTPError(req.full_url, status, '', {}, io.BytesIO(payload))
                return Response(payload)
        with patch('dataops_client.build_opener', return_value=Opener()), \
                patch('warehouse_resident._native_request') as sql:
            with patch('dataops_client.runtime_profile.private_json', return_value=dict(
                    username='owner', password='correct', selected_role='admin')):
                for action, extra in (('list', []), ('get', ['--stage-id', stage['id']])):
                    reply = stages.main([action, '--database', fixture.c['sql_catalog'],
                        '--schema', fixture.c['sql_schema'], *extra], profile=SimpleNamespace(client_auth_file='auth'))
                    self.assertEqual(reply['context'], fixture.c)
                    self.assertEqual(len(reply['context']), 9)
                    self.assertEqual(reply['stages'] if action == 'list' else reply['stage'],
                                     [stage] if action == 'list' else stage)
            sql.assert_not_called()
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(urlsplit(req.full_url).netloc == '127.0.0.1:8080' for req in calls))
        self.assertEqual([urlsplit(req.full_url).path for req in calls[:3]], [
            '/api/v1/databases', '/api/v1/databases/' + fixture.c['database_id'] + '/schemas', '/api/v1/stages'])

    def test_all_routes_full_context_role_key_and_native_default(self):
        cases = [
            ('list', [], '', False), ('get', [], '/s', False), ('files', [], '/s/files', False),
            ('runs', [], '/s/runs', False), ('exports', [], '/s/exports', False),
            ('inspect', ['--run-id', 'r/x'], '/s/runs/r%2Fx', False),
            ('cancel', ['--run-id', 'r/x', '--idempotency-key=-key'], '/s/runs/r%2Fx/cancel', True),
            ('receipt', ['--export-id', 'e'], '/s/exports/e', False),
            ('preview', ['--expected-revision', '2', '--file', 'a.csv', '--file-version', 'v', '--idempotency-key', 'k'], '/s/preview', True),
            ('import', ['--expected-revision', '2', '--file', 'a.csv', '--file-version', 'v', '--expected-preview-fingerprint', 'confirmation', '--idempotency-key', 'k'], '/s/imports', True),
            ('export', ['--expected-revision', '2', '--file', 'out.csv', '--format', 'csv', '--source', json.dumps(REF), '--idempotency-key', 'k'], '/s/exports', True),
        ]
        for action, options, suffix, post in cases:
            with self.subTest(action=action):
                self.call(self.saved(action, *([] if action == 'list' else ['--stage-id', 's']), *options))
                req = self.calls[-1]
                self.assertEqual(req.full_url.split('/api/')[0], 'http://127.0.0.1:8080')
                self.assertEqual(urlsplit(req.full_url).path, '/api/v1/stages'+suffix)
                self.assertEqual(req.get_header('X-shirokuma-role'), 'admin')
                self.assertEqual(req.method, 'POST' if post else 'GET')
                context = json.loads(req.data)['context'] if post else json.loads(parse_qs(urlsplit(req.full_url).query)['context'][0])
                self.assertEqual(context, C)
                if post:
                    self.assertTrue(req.get_header('Idempotency-key'))
                if action == 'cancel':
                    self.assertEqual(json.loads(req.data), {'context': C})
                if action == 'import':
                    self.assertEqual(json.loads(req.data)['expected_preview_fingerprint'], 'confirmation')

    def test_save_cas_rename_other_schema_and_explicit_origin(self):
        for revision, extra in [(0, []), (3, ['--stage-id', 's/x'])]:
            self.call(self.saved('save', '--definition', str(self.definition), '--expected-revision', str(revision), '--idempotency-key', 'fixed', '--api-url', 'http://127.0.0.1:8888', *extra))
            req = self.calls[-1]
            body = json.loads(req.data)
            self.assertEqual(req.full_url, 'http://127.0.0.1:8888/api/v1/stages')
            self.assertEqual(body['expected_revision'], revision)
            self.assertEqual(body['definition']['target'], REF)
            self.assertEqual(body.get('stage_id'), 's/x' if extra else None)
        self.call(['list', '--database', 'sales', '--schema', 'raw'])
        self.assertEqual(urlsplit(self.calls[-3].full_url).path, '/api/v1/databases')

    def test_download_p3_integrity_atomic_write_and_optional_metadata(self):
        output = self.root / 'download.csv'
        output.write_bytes(b'previous')
        self.call(self.saved('download', '--stage-id', 's', '--export-id', 'e', '--output', str(output)))
        self.assertEqual(output.read_bytes(), self.raw)
        self.raw = b'corrupt'
        with self.assertRaisesRegex(ValueError, 'EXPORT_INTEGRITY_FAILED'):
            self.call(self.saved('download', '--stage-id', 's', '--export-id', 'e', '--output', str(output)))
        self.assertEqual(output.read_bytes(), b'code\n001\n')
        original = dict(self.receipt)
        for metadata in ({'publication_state': 'partial'}, {'complete': False}, {'format': 'parquet'}):
            self.receipt = dict(original, **metadata)
            count = len(self.calls)
            with self.assertRaises(ValueError):
                self.call(self.saved('download', '--stage-id', 's', '--export-id', 'e', '--output', str(output)))
            self.assertEqual(len(self.calls), count+1)

    def test_download_requires_explicit_publication_and_matching_stage_metadata(self):
        original = dict(self.receipt)
        mutations = [dict(publication_state=None), dict(publication_state='PUBLISHED'), dict(stage_id='other'),
                     dict(export_id='other'), dict(stage_revision=0), dict(run_id=''), dict(file='../out.csv'),
                     dict(file=7), dict(context=dict(C, grant_revision=9)), dict(source={'ref': dict(REF, database_id='other')})]
        for metadata in mutations:
            self.receipt = dict(original, **metadata)
            self.calls.clear()
            with self.subTest(fields=list(metadata)), self.assertRaises(ValueError):
                self.call(self.saved('download', '--stage-id', 's', '--export-id', 'e', '--output', str(self.root/'out.csv')))
            self.assertEqual(len(self.calls), 1)
            self.assertFalse((self.root/'out.csv').exists())
        for field in ('publication_state', 'stage_id', 'stage_revision', 'run_id', 'file', 'context'):
            self.receipt = dict(original)
            del self.receipt[field]
            self.calls.clear()
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.call(self.saved('download', '--stage-id', 's', '--export-id', 'e', '--output', str(self.root/'out.csv')))
            self.assertEqual(len(self.calls), 1)

    def test_invalid_context_definition_paths_and_cross_database(self):
        bad = [self.saved('save', '--definition', str(self.definition), '--expected-revision', '2', '--idempotency-key', 'k'),
               self.saved('preview', '--stage-id', 's', '--expected-revision', '1', '--file', '../a.csv', '--file-version', 'v', '--idempotency-key', 'k'),
               self.saved('list', '--database', 'wrong'),
               self.saved('export', '--stage-id', 's', '--expected-revision', '1', '--file', 'out.csv', '--format', 'csv', '--source', json.dumps(dict(REF, database_id='other')), '--idempotency-key', 'k')]
        for args in bad:
            with self.subTest(action=args[0]), self.assertRaises(ValueError):
                self.call(args)
        self.assertEqual(self.calls, [])
        for change in ({'name': 'x'*49}, {'owner': 'override'}, {'format': 'parquet'}):
            definition = json.loads(self.definition.read_text())
            definition.update(change)
            with self.assertRaises(ValueError):
                stages.definition_value(definition)

    def test_admin_only_context_mismatch_and_no_retry(self):
        self.context.write_text(json.dumps(dict(C, selected_role='data_engineer')))
        with self.assertRaises(ValueError):
            self.call(self.saved('list'))
        self.assertEqual(self.calls, [])
        self.context.write_text(json.dumps(C))
        self.reply_context = dict(C, grant_revision=8)
        with self.assertRaisesRegex(ValueError, 'response context mismatch'):
            self.call(self.saved('list'))
        self.assertEqual(len(self.calls), 1)

    def test_recovery_envelope_preserved_without_private_error_fields(self):
        self.failure = {'code': 'RECORDING_UNKNOWN', 'details': {'context': C, 'run': dict(id='r', stage_id='s', context=C, status='unknown', private_blob='withheld'), 'secret': 'withheld'}}
        with self.assertRaises(stages.StageError) as error:
            self.call(self.saved('list'))
        self.assertEqual(error.exception.reply['run']['id'], 'r')
        self.assertNotIn('withheld', json.dumps(error.exception.reply))
        self.assertEqual(len(self.calls), 1)

    def test_scan_routes_retry_cas_context_and_no_automatic_replay(self):
        scan = dict(id='new', stage_id='s', stage_revision=2, owner='owner', context=C,
                    status='queued', committed=False, source_scan_id='old/x', source_item_id='item/x',
                    items=[dict(id='new-item', file='a.csv', input_fingerprint='fingerprint',
                                status='pending', attempt=4, committed=False, retryable=False)])
        for action, options, suffix in [
                ('scan', ['--expected-revision=2', '--idempotency-key=fixed'], '/scans'),
                ('scans', [], '/scans'),
                ('scan-inspect', ['--scan-id=new'], '/scans/new'),
                ('scan-retry', ['--scan-id=old/x', '--item-id=item/x', '--expected-attempt=3',
                                '--expected-revision=2', '--idempotency-key=fixed'], '/scans/old%2Fx/retry')]:
            self.scan_reply = {'scans': [scan]} if action == 'scans' else {'scan': scan}
            self.calls.clear()
            reply = self.call(self.saved(action, '--stage-id=s', *options))
            self.assertEqual(len(self.calls), 1)
            req = self.calls[0]
            self.assertEqual(urlsplit(req.full_url).path, '/api/v1/stages/s' + suffix)
            self.assertEqual(req.get_header('X-shirokuma-role'), 'admin')
            if action in ('scan', 'scan-retry'):
                body = json.loads(req.data)
                self.assertEqual(body['context'], C)
                self.assertEqual(body['expected_revision'], 2)
                self.assertEqual(req.get_header('Idempotency-key'), 'fixed')
                if action == 'scan-retry':
                    self.assertEqual(body, dict(context=C, expected_revision=2, expected_attempt=3, item_id='item/x'))
            self.assertEqual(reply['context'], C)
        for changed in (dict(id='old/x'), dict(source_item_id='other'), dict(items=[]),
                        dict(items=[dict(scan['items'][0], attempt=3)]), dict(context=dict(C, grant_revision=8))):
            self.scan_reply = {'scan': dict(scan, **changed)}
            self.calls.clear()
            with self.assertRaises(ValueError):
                self.call(self.saved('scan-retry', '--stage-id=s', '--scan-id=old/x', '--item-id=item/x',
                                    '--expected-attempt=3', '--expected-revision=2', '--idempotency-key=fixed'))
            self.assertEqual(len(self.calls), 1)

    def test_scan_strict_flags_admin_and_sanitized_failure(self):
        base = self.saved('scan-retry', '--stage-id=s', '--scan-id=old', '--item-id=i',
                          '--expected-revision=2', '--idempotency-key=k')
        for attempt in ('0', '-1'):
            with self.assertRaises(ValueError):
                self.call([*base, '--expected-attempt=' + attempt])
        self.assertEqual(self.calls, [])
        for action in ('scan', 'scans', 'scan-inspect', 'scan-retry'):
            with self.subTest(action=action), self.assertRaises(SystemExit):
                self.call(self.saved(action))
        self.context.write_text(json.dumps(dict(C, selected_role='data_engineer')))
        with self.assertRaises(ValueError):
            self.call(self.saved('scan', '--stage-id=s', '--expected-revision=2', '--idempotency-key=k'))
        self.assertEqual(self.calls, [])
        self.context.write_text(json.dumps(C))
        self.failure = {'code': 'UNKNOWN', 'details': {'scan': {'id': 'unvalidated'}, 'secret': 'password'}}
        with self.assertRaises(stages.StageError) as error:
            self.call(self.saved('scan', '--stage-id=s', '--expected-revision=2', '--idempotency-key=k'))
        self.assertIsNone(error.exception.reply)
        self.assertNotIn('password', str(error.exception))
        self.assertEqual(len(self.calls), 1)

    def test_minimal_duplicate_import_has_no_execution_identity_or_old_context(self):
        args = self.saved('import', '--stage-id=s', '--expected-revision=2', '--file=a.csv',
                          '--file-version=v', '--expected-preview-fingerprint=f', '--idempotency-key=k')
        self.run_reply = dict(id=None, status='skipped', committed=True, deduplicated=True, context=C)
        self.assertEqual(self.call(args)['run'], self.run_reply)
        for change in (dict(summary={'old': 'protected'}), dict(operation_id='fake'),
                       dict(context=dict(C, grant_revision=8)), dict(deduplicated=False)):
            self.run_reply = dict(id=None, status='skipped', committed=True, deduplicated=True, context=C, **{}) | change
            with self.assertRaises(ValueError):
                self.call(args)


if __name__ == '__main__':
    unittest.main()
