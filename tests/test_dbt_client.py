"""Real loopback HTTP, saved authentication, durable ACK and explicit recovery."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import dbt_client as client

ID = '11111111-1111-1111-1111-111111111111'
PLAN = '22222222-2222-2222-2222-222222222222'
PUB = '33333333-3333-3333-3333-333333333333'
C = dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales', sql_schema='sandbox',
         selected_role='data_engineer', binding_revision=1, generation_id='gen', grant_revision=3)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        auth = Path(self.temp.name) / 'auth.json'
        auth.write_text(json.dumps(dict(username='alice', password='private-password', selected_role='data_engineer')))
        auth.chmod(0o600)
        self.profile = SimpleNamespace(client_auth_file=str(auth))
        self.calls, self.receipts = [], {}
        self.drop, self.corrupt, self.envelope = False, None, None
        self.http_status, self.raw_response = None, None
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.handle_api()
            def do_POST(self): self.handle_api()
            def handle_api(self):
                url = urlsplit(self.path)
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0')))) if self.command == 'POST' else None
                outer.calls.append((self.command, url.path, parse_qs(url.query), body, dict(self.headers)))
                if outer.http_status:
                    self.send_response(outer.http_status)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if url.path == '/api/v1/databases':
                    reply = {'items': [dict(id='db', sql_catalog='sales', binding_revision=1, generation_id='gen', grant_revision=3)]}
                elif url.path == '/api/v1/databases/db/schemas':
                    reply = {'items': [dict(id='sc', sql_name='sandbox')]}
                else:
                    context = body['context'] if body else json.loads(parse_qs(url.query)['context'][0])
                    record = dict(id=ID, kind='run', actor='alice', context=deepcopy(context), state='QUEUED', plan_id=PLAN, project_id='lake_marts')
                    family = url.path.split('/')[4]
                    field = {'projects': 'projects', 'plans': 'plan', 'publications': 'publication'}.get(family, 'run')
                    if field == 'plan': record.update(id=PLAN, kind='plan')
                    if field == 'publication' or url.path.endswith('/publish'):
                        field = 'publication'
                        record.update(id=PUB, kind='publication', run_id=ID, parent_run_id=ID, node_id='model.lake.orders')
                        if body and 'expected_target' in body:
                            record.update(origin='native_table', expected_target=deepcopy(body['expected_target']))
                    if body and body.get('executor') in ('native_table', 'native_graph'):
                        record.update(executor=body['executor'])
                        if body['executor'] == 'native_graph':
                            record.update(source_digest='saved-digest', native_tests=body.get('native_tests', False), native_verified=False, publication_allowed=False, models=[dict(unique_id='model.graph.table', materialized='table'), dict(unique_id='model.graph.logical', materialized='ephemeral')], tests=[dict(unique_id='test.source')] if body.get('native_tests') else [])
                        if body['executor'] == 'native_table':
                            record['selected_node_id'] = body['node_id']
                        if body.get('native_tests') is True:
                            record['native_tests'] = True
                    if self.command == 'POST':
                        key = self.headers['Idempotency-Key']
                        record = outer.receipts.setdefault(key, record)
                    record = deepcopy(record)
                    if outer.corrupt: outer.corrupt(record)
                    reply = dict(context=context, **{field: record})
                    if field == 'projects': reply[field] = [{'id': 'lake_marts', 'name': 'Lake Marts'}]
                    if self.command == 'GET' and url.path.endswith(('/plans', '/runs')):
                        reply = dict(context=context, items=[record])
                if outer.envelope: reply = outer.envelope(reply)
                if outer.drop and self.command == 'POST':
                    outer.drop = False
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                raw = outer.raw_response if outer.raw_response is not None else json.dumps(reply).encode()
                self.send_response(202 if self.command == 'POST' else 200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        def stop():
            self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.addCleanup(stop)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def call(self, action, *flags, context=C):
        return client.main([action, '--api-url=' + self.url, '--context=' + json.dumps(context), *flags], profile=self.profile)

    def test_graph_request_ack_quality_and_verbatim_recovery(self):
        flags = ['--plan-id=' + PLAN, '--executor=native_graph']
        for quality in (False, True):
            argv = flags + (['--native-tests'] if quality else [])
            before = len(self.calls)
            reply = self.call('run', *argv)
            self.assertEqual(len(self.calls), before + 1)
            self.assertEqual(reply['run']['executor'], 'native_graph')
            self.assertNotIn('node_id', self.calls[-1][3])
            self.assertEqual(self.calls[-1][3], dict(context=C, plan_id=PLAN, executor='native_graph', **({'native_tests': True} if quality else {})))
            for change in (dict(executor='native_table'), dict(selected_node_id='model.wrong.node'), dict(native_tests=not quality), dict(native_tests=1), dict(plan_id=ID)):
                self.corrupt = lambda r, c=change: r.update(c)
                reply = self.call('run', *argv, '--idempotency-key=graph-' + str(quality))
                self.assertTrue(reply['request_uncertain'])
                self.assertEqual(reply['retry']['body'], self.calls[-1][3])
                self.assertNotIn('node_id', reply['retry']['intent'])
            self.corrupt = None
        self.drop = True
        reply = self.call('run', *flags, '--native-tests', '--idempotency-key=lost-graph')
        self.assertTrue(reply['request_uncertain'])
        first = self.calls[-1]
        before = len(self.calls)
        reply = self.call('run', *flags, '--native-tests', '--idempotency-key=lost-graph')
        self.assertEqual(len(self.calls), before + 1)
        self.assertEqual(first[3:], self.calls[-1][3:])
        self.assertEqual(reply['run']['executor'], 'native_graph')
        before = len(self.calls)
        for node in ('model.valid.node', ''):
            with self.assertRaises(ValueError):
                self.call('run', *flags, '--node-id=' + node)
        self.assertEqual(len(self.calls), before)
        self.corrupt = lambda r: r.update(executor='native_graph', source_digest='saved-digest', native_tests=False, native_verified=False, publication_allowed=False, models=[dict(unique_id='model.graph.table', materialized='table')], tests=[])
        for action, args in [('status', ['--run-id=' + ID]), ('runs', ['--plan-id=' + PLAN])]:
            before = len(self.calls)
            self.call(action, *args)
            self.assertEqual(len(self.calls), before + 1)
            self.assertEqual(self.calls[-1][0], 'GET')

    def test_sqlwap_false_preserves_original_body_key_and_explicit_recovery(self):
        for executor in (None, 'sql_wap'):
            flags = ['--plan-id=' + PLAN, '--idempotency-key=original-sqlwap']
            if executor:
                flags.append('--executor=' + executor)
            reply = self.call('run', *flags)
            self.assertEqual(reply['run']['id'], ID)
            self.assertEqual(self.calls[-1][3], dict(context=C, plan_id=PLAN))
            self.assertEqual(self.calls[-1][4]['Idempotency-Key'], 'original-sqlwap')
        api = client.DbtClient(self.profile, self.url)
        api.context = deepcopy(C)
        body = dict(plan_id=PLAN, native_tests=False)
        self.drop = True
        with self.assertRaises(client.RequestUncertain):
            api.post('/api/v1/dbt/runs', body, 'original-sqlwap')
        original = deepcopy(self.calls[-1])
        reply = api.post('/api/v1/dbt/runs', body, 'original-sqlwap')
        self.assertEqual(reply['run']['id'], ID)
        self.assertEqual(self.calls[-1], original)
        self.assertEqual(self.calls[-1][3], dict(context=C, **body))
        self.assertNotIn('native_tests', reply['run'])

    def test_native_quality_boolean_ack_and_frozen_uncertainty(self):
        flags = ['--plan-id=' + PLAN, '--executor=native_table', '--node-id=model.lake.orders']
        for selected in (False, True):
            argv = flags + (['--native-tests'] if selected else [])
            reply = self.call('run', *argv)
            self.assertIs(reply['run'].get('native_tests', False), selected)
            self.assertEqual('native_tests' in self.calls[-1][3], selected)
            for value in (None, 0, 1, 'true', not selected):
                self.corrupt = lambda r, v=value: r.update(native_tests=v)
                before = len(self.calls)
                reply = self.call('run', *argv, '--idempotency-key=quality-' + str(selected))
                self.assertTrue(reply['request_uncertain'])
                self.assertEqual(len(self.calls), before + 1)
                self.assertIs(reply['retry']['intent'].get('native_tests', False), selected)
                self.assertEqual(reply['retry']['body'], self.calls[-1][3])
            self.corrupt = None
        self.drop = True
        reply = self.call('run', *flags, '--native-tests', '--idempotency-key=lost-quality')
        self.assertTrue(reply['request_uncertain'])
        self.assertIs(reply['retry']['intent']['native_tests'], True)
        self.assertEqual(reply['retry']['idempotency_key'], 'lost-quality')
        before = len(self.calls)
        with self.assertRaises(ValueError):
            self.call('run', '--plan-id=' + PLAN, '--native-tests')
        self.assertEqual(len(self.calls), before)

    def test_native_publication_ack_nested_bool_integer_mismatch_retains_request(self):
        admin = dict(C, selected_role='admin')
        ref = dict(database_id='db', schema_id='marts', object_id='target', incarnation=1)
        descriptor = dict(version=1, destination=ref, target=dict(ref=ref, snapshot='7',
            schema_id=0, columns=[dict(name='id', type='BIGINT')], schema_fingerprint='f' * 64))
        preview = dict(version=1, origin='native_table', run_id=ID, plan_id=PLAN,
            node_id='model.lake.orders', context=admin, expected_target=descriptor)
        path = Path(self.temp.name) / 'review.json'
        path.write_text(json.dumps(preview))
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders',
                 '--expected-target-file=' + str(path), '--idempotency-key=original-review']
        for change in (lambda d: d.update(version=True),
                       lambda d: d['destination'].update(incarnation=True),
                       lambda d: d['target']['ref'].update(incarnation=True),
                       lambda d: d['target'].update(schema_id=False)):
            with self.subTest(change=change):
                self.corrupt = lambda record: change(record['expected_target'])
                before = len(self.calls)
                result = self.call('publish', *flags, context=admin)
                self.assertEqual(len(self.calls), before + 1)
                self.assertTrue(result['request_uncertain'])
                self.assertEqual(result['retry']['body'], dict(context=admin,
                    node_id='model.lake.orders', expected_target=descriptor))
                self.assertEqual(result['retry']['intent']['expected_target'], descriptor)
                self.assertEqual(result['retry']['idempotency_key'], 'original-review')
        self.corrupt = None
        result = self.call('publish', *flags, context=admin)
        self.assertEqual(result['publication']['expected_target'], descriptor)
        self.assertEqual(self.calls[-1][4]['Idempotency-Key'], 'original-review')

    def test_native_preview_file_exact_descriptor_ack_and_recovery(self):
        admin = dict(C, selected_role='admin')
        preview = dict(version=1, origin='native_table', run_id=ID, plan_id=PLAN, node_id='model.lake.orders',
                       context=admin, expected_target=dict(version=1,
                       destination=dict(database_id='db', schema_id='marts', physical_name='saved_alias'), target=None))
        self.envelope = lambda reply: dict(context=admin, preview=deepcopy(preview))
        result = self.call('publish-preview', '--run-id=' + ID, '--node-id=model.lake.orders', context=admin)
        self.assertEqual(result['preview'], preview)
        self.assertEqual(self.calls[-1][0], 'GET')
        self.assertIsNone(self.calls[-1][3])
        self.assertEqual(self.calls[-1][2]['node_id'], ['model.lake.orders'])
        self.assertNotIn('Idempotency-Key', self.calls[-1][4])
        self.envelope = None
        path = Path(self.temp.name) / 'review.json'
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders', '--expected-target-file=' + str(path)]
        for envelope in (preview, dict(context=admin, preview=preview)):
            path.write_text(json.dumps(envelope))
            result = self.call('publish', *flags, context=admin)
            self.assertEqual(result['publication']['expected_target'], preview['expected_target'])
            self.assertEqual(set(self.calls[-1][3]), {'node_id', 'context', 'expected_target'})
        original = deepcopy(preview['expected_target'])
        ref = dict(database_id='db', schema_id='marts', object_id='target', incarnation=1)
        preview['expected_target'] = dict(version=1, destination=ref, target=dict(ref=ref, snapshot='7',
            schema_id=0, columns=[dict(name='id', type='BIGINT')], schema_fingerprint='f' * 64))
        path.write_text(json.dumps(preview))
        result = self.call('publish', *flags, context=admin)
        self.assertEqual(result['publication']['expected_target'], preview['expected_target'])
        preview['expected_target'] = original
        for field, value in [('origin', 'sql_wap'), ('run_id', PLAN), ('node_id', 'model.other'),
                             ('version', True), ('context', C), ('expected_target', {})]:
            path.write_text(json.dumps(dict(preview, **{field: value})))
            before = len(self.calls)
            with self.assertRaises(ValueError):
                self.call('publish', *flags, context=admin)
            self.assertEqual(len(self.calls), before)
        path.write_text(json.dumps(preview))
        for change in (lambda r: r.update(origin='sql_wap'), lambda r: r.update(expected_target={}),
                       lambda r: r.update(plan_id=ID), lambda r: r.update(parent_run_id=PLAN),
                       lambda r: r['expected_target'].update(version=True)):
            self.corrupt = change
            result = self.call('publish', *flags, '--idempotency-key=retained', context=admin)
            self.assertTrue(result['request_uncertain'])
            self.assertEqual(result['retry']['body'], self.calls[-1][3])
            self.assertEqual(result['retry']['intent']['expected_target'], preview['expected_target'])
            self.assertEqual(result['retry']['idempotency_key'], 'retained')
        self.corrupt = None
        self.drop = True
        result = self.call('publish', *flags, '--idempotency-key=lost-publication', context=admin)
        before = len(self.calls)
        self.assertTrue(result['request_uncertain'])
        self.call('status', '--run-id=' + ID, context=admin)
        self.assertEqual(len(self.calls), before + 1)
        self.assertEqual(self.calls[-1][0], 'GET')
        self.http_status = 409
        result = self.call('publish', *flags, '--idempotency-key=lost-publication', context=admin)
        self.assertTrue(result['request_uncertain'])

    def test_commands_and_saved_auth(self):
        cases = [('projects', []), ('compile', []), ('plans', []), ('plan', ['--plan-id=' + PLAN]),
                 ('run', ['--plan-id=' + PLAN]), ('runs', ['--plan-id=' + PLAN]), ('status', ['--run-id=' + ID]),
                 ('cancel', ['--run-id=' + ID]), ('reconcile', ['--run-id=' + ID]),
                 ('publish', ['--run-id=' + ID, '--node-id=model.lake.orders']), ('publication', ['--publication-id=' + PUB])]
        for action, flags in cases:
            with self.subTest(action=action):
                self.call(action, *flags)
                method, path, params, body, headers = self.calls[-1]
                self.assertEqual(headers['Authorization'], 'Basic ' + base64.b64encode(b'alice:private-password').decode())
                self.assertEqual(headers['X-Shirokuma-Role'], 'data_engineer')
                if method == 'POST':
                    self.assertTrue(headers['Idempotency-Key'])
                    self.assertEqual(body['context'], C)
                else:
                    self.assertEqual(json.loads(params['context'][0]), C)
        self.assertEqual(len(self.calls), len(cases))

    def test_default_schema_and_context_file(self):
        client.main(['projects', '--database=sales', '--api-url=' + self.url], profile=self.profile)
        self.assertEqual(json.loads(self.calls[-1][2]['context'][0]), C)
        path = Path(self.temp.name) / 'context.json'; path.write_text(json.dumps({'context': C}))
        client.main(['status', '--context-file=' + str(path), '--run-id=' + ID, '--api-url=' + self.url], profile=self.profile)
        self.assertEqual(self.calls[-1][1], '/api/v1/dbt/runs/' + ID)

    def test_lost_ack_has_original_intent_and_no_hidden_replay(self):
        self.drop = True
        result = self.call('run', '--plan-id=' + PLAN)
        self.assertTrue(result['request_uncertain'])
        retry = result['retry']
        self.assertEqual(retry['context'], C)
        self.assertEqual(retry['intent'], {'plan_id': PLAN})
        self.assertEqual(len(self.calls), 1)
        self.call('status', '--run-id=' + ID)
        self.assertEqual([r[0] for r in self.calls], ['POST', 'GET'])
        reply = self.call('run', '--plan-id=' + PLAN, '--idempotency-key=' + retry['idempotency_key'])
        self.assertEqual(reply['run']['id'], ID)
        self.assertEqual(len(self.receipts), 1)
        self.assertEqual(self.calls[0][3], self.calls[-1][3])

    def test_reject_forged_artifact_before_output(self):
        for change in [lambda r: r.update(id=PLAN), lambda r: r.update(kind='plan'),
                       lambda r: r['context'].update(database_id='foreign')]:
            self.corrupt = change
            with self.assertRaises(ValueError):
                self.call('status', '--run-id=' + ID)
        self.corrupt = lambda r: r.update(plan_id=ID)
        self.assertTrue(self.call('run', '--plan-id=' + PLAN)['request_uncertain'])

    def test_compile_lost_ack_and_forged_associations(self):
        self.drop = True
        reply = self.call('compile', '--idempotency-key=original-compile')
        self.assertEqual(reply['retry']['idempotency_key'], 'original-compile')
        self.assertEqual(reply['retry']['intent'], dict(project_id='lake_marts', source_schema='raw', target_schema='marts', staging_materialization=None))
        self.assertEqual(len(self.calls), 1)
        self.call('compile', '--idempotency-key=original-compile')
        self.assertEqual(len(self.receipts), 1)
        for action, flags, change in [
            ('compile', [], lambda r: r.update(project_id='other')),
            ('publish', ['--run-id=' + ID, '--node-id=model.lake.orders'], lambda r: r.update(run_id=PLAN)),
            ('publication', ['--publication-id=' + PUB], lambda r: r.update(id=ID)),
            ('status', ['--run-id=' + ID], lambda r: r.update(models=[{'candidate': {'ref': {'database_id': 'foreign'}}}]))]:
            self.corrupt = change
            with self.subTest(action=action):
                if action in ('compile', 'publish'):
                    self.assertTrue(self.call(action, *flags)['request_uncertain'])
                else:
                    with self.assertRaises(ValueError): self.call(action, *flags)

    def test_admin_observes_engineer_saved_role(self):
        def engineer(record):
            record.update(actor='engineer')
            record['context'].update(selected_role='data_engineer', grant_revision=2)
        self.corrupt = engineer
        reply = self.call('status', '--run-id=' + ID, context=dict(C, selected_role='admin'))
        self.assertEqual(reply['run']['context']['selected_role'], 'data_engineer')
        self.assertEqual(reply['run']['actor'], 'engineer')
        # Reconciliation of the administrator's publication child returns its
        # engineer-owned parent, rather than a newly created admin artifact.
        reply = self.call('reconcile', '--run-id=' + ID, context=dict(C, selected_role='admin'))
        self.assertEqual(reply['run']['actor'], 'engineer')
        for change in (lambda r: r.update(id=PLAN),
                       lambda r: r['context'].update(database_id='foreign'),
                       lambda r: r['context'].update(schema_id='other')):
            def invalid(record):
                engineer(record)
                change(record)
            self.corrupt = invalid
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.call('status', '--run-id=' + ID, context=dict(C, selected_role='admin'))
                self.assertTrue(self.call('reconcile', '--run-id=' + ID,
                                         context=dict(C, selected_role='admin'))['request_uncertain'])

    def test_engineer_rejects_other_owner_on_get_and_post(self):
        self.corrupt = lambda r: r.update(actor='someone_else')
        for action, flags in [('status', ['--run-id=' + ID]), ('plans', []), ('runs', [])]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.call(action, *flags)
        self.assertTrue(self.call('run', '--plan-id=' + PLAN)['request_uncertain'])

    def test_admin_mutations_require_own_exact_context(self):
        for change in (lambda r: r.update(actor='someone_else'),
                       lambda r: r['context'].update(selected_role='data_engineer'),
                       lambda r: r['context'].update(grant_revision=2)):
            self.corrupt = change
            for action, flags in [('compile', []), ('run', ['--plan-id=' + PLAN]),
                                  ('publish', ['--run-id=' + ID, '--node-id=model.lake.orders']),
                                  ('cancel', ['--run-id=' + ID])]:
                with self.subTest(action=action, change=change):
                    reply = self.call(action, *flags, context=dict(C, selected_role='admin'))
                    self.assertTrue(reply['request_uncertain'])
                    self.assertNotIn('run', reply)
                    self.assertNotIn('plan', reply)
                    self.assertNotIn('publication', reply)

    def test_rejected_post_response_preserves_generated_intent_for_explicit_recovery(self):
        cases = [('cancel', ['--run-id=' + ID], change) for change in (
            lambda r: r['context'].update(database_id='foreign'),
            lambda r: r.update(id=PLAN), lambda r: r.update(actor='someone_else'),
            lambda r: r.update(kind='plan'), lambda r: r['context'].update(grant_revision=4))]
        cases.append(('run', ['--plan-id=' + PLAN], lambda r: r.update(plan_id=ID)))
        for action, flags, change in cases:
            with self.subTest(change=change):
                self.calls.clear(); self.receipts.clear()
                self.corrupt = change
                reply = self.call(action, *flags)
                self.assertEqual(set(reply), {'request_uncertain', 'retry'})
                retry = reply['retry']
                self.assertEqual(retry['method'], 'POST')
                self.assertEqual(retry['path'], self.calls[0][1])
                self.assertEqual(retry['context'], C)
                self.assertEqual(retry['body'], self.calls[0][3])
                self.assertEqual(retry['intent'], {k: v for k, v in self.calls[0][3].items() if k != 'context'})
                self.assertEqual(retry['idempotency_key'], self.calls[0][4]['Idempotency-Key'])
                self.assertEqual(len(self.calls), 1)
                self.corrupt = None
                recovered = self.call(action, *flags, '--idempotency-key=' + retry['idempotency_key'])
                self.assertEqual(recovered['run']['id'], ID)
                self.assertEqual(len(self.receipts), 1)
                self.assertEqual(self.calls[0][3], self.calls[1][3])

    def test_envelope_and_json_rejection_preserve_post_but_get_fails(self):
        for mode in ('context', 'envelope', 'artifact', 'json', 'encoding'):
            self.envelope, self.raw_response = None, None
            if mode == 'context':
                self.envelope = lambda r: dict(r, context=dict(C, database_id='foreign'))
            elif mode == 'envelope': self.envelope = lambda r: []
            elif mode == 'artifact': self.envelope = lambda r: dict(context=C)
            else: self.raw_response = b'{' if mode == 'json' else b'\xff'
            with self.subTest(mode=mode):
                reply = self.call('run', '--plan-id=' + PLAN)
                self.assertEqual(set(reply), {'request_uncertain', 'retry'})
                self.assertEqual(reply['retry']['body'], self.calls[-1][3])
                with self.assertRaises(ValueError): self.call('status', '--run-id=' + ID)

    def test_deterministic_http_rejection_is_not_uncertain(self):
        for status in (400, 401, 403, 404, 422):
            self.http_status = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'DBT_REQUEST_FAILED'):
                self.call('run', '--plan-id=' + PLAN)
        self.assertEqual(self.receipts, {})
        for status in (409, 503):
            self.http_status = status
            reply = self.call('run', '--plan-id=' + PLAN, '--executor=native_graph')
            self.assertTrue(reply['request_uncertain'])
            self.assertEqual(reply['retry']['body'], self.calls[-1][3])

    def test_native_flags_validate_locally_and_preserve_uncertain_intent(self):
        for flags in [[], ['--node-id=bad node'], ['--node-id=']]:
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.call('run', '--plan-id=' + PLAN, '--executor=native_table', *flags)
        with self.assertRaises(ValueError):
            self.call('run', '--plan-id=' + PLAN, '--node-id=model.any.table')
        self.assertEqual(self.calls, [])
        self.drop = True
        result = self.call('run', '--plan-id=' + PLAN, '--executor=native_table', '--node-id=model.any.table')
        self.assertTrue(result['request_uncertain'])
        retry = result['retry']
        self.assertEqual(retry['intent'], dict(plan_id=PLAN, executor='native_table', node_id='model.any.table'))
        self.assertEqual(retry['body'], dict(retry['intent'], context=C))
        reply = self.call('run', '--plan-id=' + PLAN, '--executor=native_table', '--node-id=model.any.table',
                          '--idempotency-key=' + retry['idempotency_key'])
        self.assertEqual(reply['run']['selected_node_id'], 'model.any.table')
        self.assertEqual(self.calls[0][3], self.calls[1][3])
        self.assertEqual(len(self.receipts), 1)

    def test_native_returned_executor_and_selected_node_must_match_intent(self):
        for index, change in enumerate([lambda r: r.update(executor='sql_wap'),
                lambda r: r.pop('executor'), lambda r: r.update(selected_node_id='model.foreign.table'),
                lambda r: r.pop('selected_node_id')]):
            self.corrupt = change
            result = self.call('run', '--plan-id=' + PLAN, '--executor=native_table', '--node-id=model.any.table',
                               '--idempotency-key=association-' + str(index))
            self.assertTrue(result['request_uncertain'])
            self.assertEqual(result['retry']['intent']['node_id'], 'model.any.table')


if __name__ == '__main__': unittest.main()
