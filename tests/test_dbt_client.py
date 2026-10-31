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


def graph_publication_fixture():
    """Public saved receipts for two TABLEs, an EPHEMERAL, and the whole suite."""
    mids = ['model.lake.stage', 'model.lake.orders', 'model.lake.second']
    tids = ['test.orders', 'test.second', 'test.source', 'test.unattached']
    models = [dict(unique_id=uid, materialized='ephemeral' if i == 0 else 'table',
        table_properties={} if i == 0 else dict(format='PARQUET'),
        depends_on=['source.orders'] if i == 0 else [mids[i - 1]],
        relation=['sales', 'sandbox', ['stage', 'orders', 'second'][i]]) for i, uid in enumerate(mids)]
    plan = dict(id=PLAN, kind='plan', actor='engineer', context=deepcopy(C), state='READY',
        source_digest='saved-graph-digest', target_schema='marts', models=deepcopy(models),
        tests=[dict(unique_id=uid) for uid in tids])
    run = dict(id=ID, kind='run', actor='engineer', context=deepcopy(C), state='NATIVE_VERIFIED',
        plan_id=PLAN, source_digest=plan['source_digest'], executor='native_graph', native_tests=True,
        native_verified=True, tests_status='PASSED', publication_allowed=True, models=models,
        tests=[dict(unique_id=uid, state='PASSED', failure_count=0) for uid in tids], operations=[])
    build, tests = [], []
    for i, model in enumerate(models):
        uid = model['unique_id']
        sdk = dict(node_id=uid, status='success', failures=None,
            adapter_response={} if i == 0 else dict(code='CREATE TABLE', rows_affected=0))
        build.append(sdk)
        model.update(state='EXPANDED' if i == 0 else 'NATIVE_VERIFIED', candidate=None, operation_ids=[])
        if i:
            model['candidate'] = dict(ref=dict(database_id='db', schema_id='sc', object_id='candidate-' + str(i), incarnation=1), snapshot=str(10 + i))
            operation_id = 'build-' + uid
            model['operation_ids'] = [operation_id]
            model['native_receipt'] = dict(executor='native_graph', plan_id=PLAN, node_id=uid, phase='build', digest='plan-digest',
                native_verified=True, sdk_result=deepcopy(sdk), candidate=deepcopy(model['candidate']))
            run['operations'].append(dict(id=operation_id, phase='build', node_id=uid, state='FINISHED', committed=True))
    for test in run['tests']:
        uid = test['unique_id']; operation_id = 'test-' + uid
        sdk = dict(node_id=uid, status='pass', failures=0, adapter_response=dict(code='SELECT', rows_affected=1))
        tests.append(sdk)
        test.update(operation_id=operation_id, native_receipt=dict(executor='native_graph', plan_id=PLAN, node_id=uid, phase='test',
            digest='plan-digest', test_verified=True, status='PASSED', failures=0, sdk_result=deepcopy(sdk)))
        run['operations'].append(dict(id=operation_id, phase='test', node_id=uid, state='FINISHED', committed=False))
    run['task_receipts'] = dict(build=dict(task='RunTask', completed=True, success=True, selected=sorted(mids),
        nodes=deepcopy(build), results=deepcopy(build[1:])), test=dict(task='TestTask', completed=True, success=True,
        selected=sorted(tids), nodes=deepcopy(tests), results=deepcopy(tests)))
    admin = dict(C, selected_role='admin')
    preview = dict(version=1, origin='native_graph', run_id=ID, plan_id=PLAN, node_id=mids[1], context=admin,
        source_digest=run['source_digest'], source_actor=run['actor'], source_context=deepcopy(C), publisher_actor='alice',
        candidate=deepcopy(models[1]['candidate']), quality=dict(status='PASSED', count=len(tids), test_ids=tids),
        target=dict(catalog='sales', schema='marts', name='orders'),
        expected_target=dict(version=1, destination=dict(database_id='db', schema_id='marts', physical_name='orders'), target=None))
    return run, plan, preview


def view_graph_fixture():
    run, plan, preview = graph_publication_fixture()
    model = run['models'][1]
    model.update(materialized='view', table_properties={}, view_security='INVOKER')
    plan['models'][1].update(materialized='view', table_properties={}, view_security='INVOKER')
    model['candidate'] = dict(kind='VIEW', ref=deepcopy(model['candidate']['ref']),
        view_uuid='44444444-4444-4444-4444-444444444444', version=1, security='INVOKER')
    sdk = next(x for x in run['task_receipts']['build']['nodes'] if x['node_id'] == model['unique_id'])
    sdk.update(adapter_response=dict(code='CREATE VIEW'), model_type='view', view_security='invoker')
    run['task_receipts']['build']['results'][0] = deepcopy(sdk)
    model['native_receipt'].update(candidate=deepcopy(model['candidate']), sdk_result=deepcopy(sdk))
    run['publication_allowed'] = True
    preview['views'] = [dict(node_id=model['unique_id'], candidate=deepcopy(model['candidate']), definition_digest='a' * 64)]
    preview.update(node_id=run['models'][2]['unique_id'], candidate=deepcopy(run['models'][2]['candidate']))
    preview['target']['name'] = preview['expected_target']['destination']['physical_name'] = 'second'
    return run, plan, preview


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
                    raw = outer.raw_response or b''
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if url.path == '/api/v1/databases':
                    reply = {'items': [dict(id='db', sql_catalog='sales', binding_revision=1, generation_id='gen', grant_revision=3)]}
                elif url.path == '/api/v1/databases/db/schemas':
                    reply = {'items': [dict(id='sc', sql_name='sandbox'), dict(id='marts', sql_name='marts')]}
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

    def test_view_graph_saved_receipts_status_history_and_request_contract(self):
        run, plan, _ = view_graph_fixture()
        run['actor'] = plan['actor'] = 'alice'
        def envelope(reply):
            path = self.calls[-1][1]
            if path.endswith('/projects'):
                return reply
            if '/plans/' in path:
                return dict(context=C, plan=deepcopy(plan))
            return dict(context=C, **({'items': [deepcopy(run)]} if self.calls[-1][0] == 'GET' and path.endswith('/runs') else {'run': deepcopy(run)}))
        self.envelope = envelope
        self.call('projects')
        self.assertEqual(self.call('plan', '--plan-id=' + PLAN)['plan'], plan)
        for quality in (False, True):
            saved = deepcopy(run)
            if not quality:
                run.update(native_tests=False, tests_status='NOT_RUN', tests=[])
            reply = self.call('run', '--plan-id=' + PLAN, '--executor=native_graph', *(['--native-tests'] if quality else []), '--idempotency-key=view-' + str(quality))
            self.assertEqual(reply['run'], run)
            self.assertEqual(self.calls[-1][3], dict(context=C, plan_id=PLAN, executor='native_graph', **({'native_tests': True} if quality else {})))
            run = saved
        before = len(self.calls)
        self.assertEqual(self.call('status', '--run-id=' + ID)['run'], run)
        self.assertEqual(self.call('runs', '--plan-id=' + PLAN)['items'], [run])
        self.assertTrue(all(c[0] == 'GET' for c in self.calls[before:]))
        self.assertIn('task_receipts', run)
        self.assertNotIn('graph_task_receipts', run)
        self.assertNotIn('snapshot', run['models'][1]['candidate'])

    def test_view_graph_malformed_identity_and_receipt_ack_retains_intent(self):
        run, _, _ = view_graph_fixture()
        run['actor'] = 'alice'
        changes = [lambda m: m['candidate'].pop('view_uuid'), lambda m: m['candidate'].update(view_uuid='bad'),
            lambda m: m['candidate'].update(version=True), lambda m: m['candidate'].update(version=0),
            lambda m: m['candidate'].update(security='DEFINER'), lambda m: m['candidate'].update(snapshot='fake'),
            lambda m: m.update(candidate=dict(ref=m['candidate']['ref'], snapshot='fake')),
            lambda m: m['candidate']['ref'].update(schema_id='raw'), lambda m: m['native_receipt'].update(node_id='wrong')]
        for index, change in enumerate(changes):
            bad = deepcopy(run); change(bad['models'][1])
            if bad['models'][1]['native_receipt']['node_id'] != 'wrong':
                bad['models'][1]['native_receipt']['candidate'] = deepcopy(bad['models'][1]['candidate'])
            self.envelope = lambda reply: dict(context=C, run=deepcopy(bad))
            reply = self.call('run', '--plan-id=' + PLAN, '--executor=native_graph', '--native-tests', '--idempotency-key=bad-view-' + str(index))
            self.assertTrue(reply['request_uncertain'])
            self.assertEqual(reply['retry']['body'], dict(context=C, plan_id=PLAN, executor='native_graph', native_tests=True))
            with self.assertRaises(ValueError):
                self.call('status', '--run-id=' + ID)

    def test_view_graph_full_quality_and_complete_summary_required(self):
        admin, path = self.graph_review()
        self.source, self.source_plan, self.preview = view_graph_fixture()
        flags = ['--run-id=' + ID, '--node-id=model.lake.second']
        self.assertEqual(self.call('publish-preview', *flags, context=admin)['preview']['views'], self.preview['views'])
        good = deepcopy(self.source)
        for change in (lambda r: r['tests'].pop(), lambda r: r.update(native_verified=False),
                lambda r: r['models'][1]['native_receipt'].update(sdk_result={}),
                lambda r: r['task_receipts']['build']['results'].pop(),
                lambda r: r['task_receipts']['test']['nodes'][0].update(failures=False),
                lambda r: r['models'][1].update(view_security='DEFINER')):
            self.source = deepcopy(good); change(self.source)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.call('publish-preview', *flags, context=admin)
        self.source = good
        summary = deepcopy(self.preview['views'])
        for bad in (None, [], summary * 2, [dict(summary[0], definition_digest='A' * 64)],
                    [dict(summary[0], node_id='wrong')]):
            self.preview['views'] = bad
            with self.subTest(summary=bad), self.assertRaises(ValueError):
                self.call('publish-preview', *flags, context=admin)
        self.assertTrue(all(c[0] == 'GET' for c in self.calls))

    def test_multiview_graph_summary_covers_every_physical_view(self):
        admin, _ = self.graph_review()
        run, plan, preview = view_graph_fixture()
        second = deepcopy(run['models'][1])
        second['unique_id'] = 'model.lake.extra_view'
        second['candidate']['view_uuid'] = '55555555-5555-5555-5555-555555555555'
        second['candidate']['ref']['object_id'] = 'extra-view'
        operation_id = 'build-' + second['unique_id']
        second['operation_ids'] = [operation_id]
        second['native_receipt'].update(node_id=second['unique_id'], candidate=deepcopy(second['candidate']))
        second['native_receipt']['sdk_result']['node_id'] = second['unique_id']
        run['models'].append(second)
        planned = deepcopy(plan['models'][1]); planned['unique_id'] = second['unique_id']
        plan['models'].append(planned)
        run['operations'].append(dict(id=operation_id, phase='build', node_id=second['unique_id'], state='FINISHED', committed=True))
        task = run['task_receipts']['build']
        task['selected'].append(second['unique_id'])
        for field in ('nodes', 'results'):
            task[field].append(deepcopy(second['native_receipt']['sdk_result']))
        preview['views'].append(dict(node_id=second['unique_id'], candidate=deepcopy(second['candidate']), definition_digest='b' * 64))
        self.source, self.source_plan, self.preview = run, plan, preview
        flags = ['--run-id=' + ID, '--node-id=model.lake.second']
        self.assertEqual(len(self.call('publish-preview', *flags, context=admin)['preview']['views']), 2)
        good = deepcopy(preview['views'])
        for bad in (good[:1], [good[0], dict(good[1], candidate=good[0]['candidate'])]):
            self.preview['views'] = bad
            with self.assertRaises(ValueError):
                self.call('publish-preview', *flags, context=admin)

    def test_view_graph_frozen_replay_and_history_no_source_get(self):
        admin, path = self.graph_review()
        self.source, self.source_plan, self.preview = view_graph_fixture()
        frozen = deepcopy(self.preview)
        path.write_text(json.dumps(frozen))
        flags = ['--run-id=' + ID, '--node-id=model.lake.second', '--preview-file=' + str(path), '--idempotency-key=view-frozen']
        def ack(record):
            record.update(origin='native_graph', node_id=frozen['node_id'], plan_id=PLAN, source_digest=frozen['source_digest'], views=deepcopy(frozen['views']))
        self.corrupt = ack
        self.drop = True
        result = self.call('publish', *flags, context=admin)
        self.assertTrue(result['request_uncertain'])
        original = deepcopy(self.calls[-1])
        self.source.update(state='CANCELED', publication_allowed=False)
        self.source['models'][1]['candidate']['version'] = 2
        recovered = self.call('publish', *flags, context=admin)
        self.assertEqual(recovered['publication']['views'], frozen['views'])
        self.assertEqual(self.calls[-1], original)
        for change in (lambda r: r.pop('views'), lambda r: r['views'][0].update(definition_digest='b' * 64),
                       lambda r: r['views'][0]['candidate'].update(version=True)):
            def mismatch(record):
                ack(record); change(record)
            self.corrupt = mismatch
            uncertain = self.call('publish', *flags, context=admin)
            self.assertTrue(uncertain['request_uncertain'])
            self.assertEqual(uncertain['retry']['body'], original[3])
            self.assertEqual(uncertain['retry']['idempotency_key'], 'view-frozen')
        self.corrupt = ack
        before = len(self.calls)
        self.assertEqual(self.call('publication', '--publication-id=' + PUB, context=admin)['publication']['views'], frozen['views'])
        self.assertEqual([c[0] for c in self.calls[before:]], ['GET'])
        self.assertTrue(all(c[0] == 'POST' for c in self.calls[:before]))
        intent = {k: deepcopy(frozen[k]) for k in ('origin', 'run_id', 'plan_id', 'node_id', 'source_digest', 'source_actor', 'source_context')}
        intent.update(context=admin, publisher_actor='alice', candidate=deepcopy(frozen['candidate']))
        def audited(record):
            ack(record)
            record['native_audit'] = dict(intent=deepcopy(intent))
        self.corrupt = audited
        self.call('publication', '--publication-id=' + PUB, context=admin)
        for change in (lambda i: i['source_context'].update(schema_id='other'),
                       lambda i: i.update(source_actor=''), lambda i: i.update(publisher_actor='engineer'),
                       lambda i: i.update(run_id=PLAN), lambda i: i.update(source_digest='other'),
                       lambda i: i['candidate']['ref'].update(incarnation=True),
                       lambda i: i['candidate'].update(kind='VIEW'), lambda i: i['candidate'].update(view_uuid=ID)):
            def bad_intent(record):
                audited(record); change(record['native_audit']['intent'])
            self.corrupt = bad_intent
            before = len(self.calls)
            with self.assertRaises(ValueError):
                self.call('publication', '--publication-id=' + PUB, context=admin)
            self.assertEqual(len(self.calls), before + 1)
        for change in (lambda r: r.update(parent_run_id=PLAN), lambda r: r.update(plan_id='bad'),
                       lambda r: r.update(source_digest=''), lambda r: r.update(node_id=frozen['views'][0]['node_id']),
                       lambda r: r.update(native_audit=dict(intent=dict(source_context=dict(C, schema_id='other'))))):
            def bad_history(record):
                ack(record); change(record)
            self.corrupt = bad_history
            before = len(self.calls)
            with self.assertRaises(ValueError):
                self.call('publication', '--publication-id=' + PUB, context=admin)
            self.assertEqual(len(self.calls), before + 1)


    def test_view_publication_history_binds_saved_context_after_admin_grant_change(self):
        _, _, frozen = view_graph_fixture()
        saved_admin = dict(frozen['context'], grant_revision=7)
        current_admin = dict(saved_admin, grant_revision=11)
        intent = {k: deepcopy(frozen[k]) for k in ('origin', 'run_id', 'plan_id', 'node_id',
                  'source_digest', 'source_actor', 'source_context', 'candidate')}
        intent.update(context=deepcopy(saved_admin), publisher_actor='alice')
        self.assertEqual(intent['source_context']['selected_role'], 'data_engineer')
        self.assertNotEqual(intent['source_context'], saved_admin)
        for receipt in ('native_audit', 'native_publication'):
            def record(r):
                r.update(origin='native_graph', state='PUBLISHED', context=deepcopy(saved_admin),
                         node_id=frozen['node_id'], source_digest=frozen['source_digest'],
                         views=deepcopy(frozen['views']))
                r[receipt] = dict(intent=deepcopy(intent))
            self.corrupt = record
            before = len(self.calls)
            with self.subTest(receipt=receipt):
                publication = self.call('publication', '--publication-id=' + PUB,
                                        context=current_admin)['publication']
                self.assertEqual(publication['context'], saved_admin)
                self.assertEqual(publication[receipt]['intent'], intent)
                self.assertEqual(publication['views'], frozen['views'])
                for wrong_context in (current_admin, intent['source_context']):
                    def wrong(r):
                        record(r)
                        r[receipt]['intent']['context'] = deepcopy(wrong_context)
                    self.corrupt = wrong
                    with self.assertRaisesRegex(ValueError, 'frozen publication source identity mismatch'):
                        self.call('publication', '--publication-id=' + PUB, context=current_admin)
                self.corrupt = record
                with self.assertRaises(ValueError):
                    self.call('publication', '--publication-id=' + PUB,
                              context=dict(current_admin, selected_role='data_engineer'))
            self.assertEqual([(c[0], c[1]) for c in self.calls[before:]],
                             [('GET', '/api/v1/dbt/publications/' + PUB)] * 4)
        self.assertEqual(self.receipts, {})

    def test_completed_view_publication_requires_frozen_native_intent(self):
        admin, path = self.graph_review()
        self.source, self.source_plan, self.preview = view_graph_fixture()
        frozen = deepcopy(self.preview)
        path.write_text(json.dumps(frozen))
        intent = {k: deepcopy(frozen[k]) for k in ('origin', 'run_id', 'plan_id', 'node_id', 'source_digest', 'source_actor', 'source_context', 'candidate')}
        intent.update(context=admin, publisher_actor='alice')
        for state in ('QUEUED', 'AUDITING', 'PUBLISHED', 'COMMITTED_RECORDING_FAILED'):
            for receipt in (None, 'native_audit', 'native_publication'):
                def record(r):
                    r.update(origin='native_graph', state=state, node_id=frozen['node_id'],
                             source_digest=frozen['source_digest'], views=deepcopy(frozen['views']))
                    if receipt:
                        r[receipt] = dict(intent=deepcopy(intent))
                self.corrupt = record
                before = len(self.calls)
                with self.subTest(state=state, receipt=receipt):
                    if receipt is None and state in ('PUBLISHED', 'COMMITTED_RECORDING_FAILED'):
                        with self.assertRaisesRegex(ValueError, 'missing frozen publication receipt'):
                            self.call('publication', '--publication-id=' + PUB, context=admin)
                    else:
                        self.assertEqual(self.call('publication', '--publication-id=' + PUB, context=admin)['publication']['state'], state)
                self.assertEqual([c[0] for c in self.calls[before:]], ['GET'])
                if receipt is None and state in ('PUBLISHED', 'COMMITTED_RECORDING_FAILED'):
                    before = len(self.calls)
                    result = self.call('publish', '--run-id=' + ID, '--node-id=' + frozen['node_id'],
                                       '--preview-file=' + str(path), '--idempotency-key=missing-intent', context=admin)
                    self.assertTrue(result['request_uncertain'])
                    self.assertEqual(result['retry']['idempotency_key'], 'missing-intent')
                    self.assertEqual([c[0] for c in self.calls[before:]], ['POST'])

    def test_view_publication_known_rejection_keeps_conservative_boundary(self):
        admin, path = self.graph_review()
        self.http_status = 409
        for code in ('DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED', 'DBT_PUBLICATION_REVIEW_CHANGED', 'DBT_VIEW_CHANGED', 'OTHER_CONFLICT'):
            self.raw_response = json.dumps(dict(error=dict(code=code))).encode()
            flags = ['--run-id=' + ID, '--node-id=model.lake.orders', '--expected-target-file=' + str(path), '--idempotency-key=original-view-publish']
            if code == 'OTHER_CONFLICT':
                self.assertTrue(self.call('publish', *flags, context=admin)['request_uncertain'])
            else:
                with self.assertRaisesRegex(client.RequestRejected, code):
                    self.call('publish', *flags, context=admin)
            # This code cannot close a graph build or an unreviewed publication conflict.
            self.assertTrue(self.call('run', '--plan-id=' + PLAN, '--executor=native_graph')['request_uncertain'])
            self.assertTrue(self.call('publish', '--run-id=' + ID, '--node-id=model.lake.orders', context=admin)['request_uncertain'])
        self.raw_response = json.dumps(dict(error=dict(code='DBT_VIEW_CHANGED'))).encode()
        for status in (400, 500):
            self.http_status = status
            self.assertTrue(self.call('publish', *flags, context=admin)['request_uncertain'])
        self.assertTrue(all(c[0] == 'POST' for c in self.calls))
        self.assertEqual(self.receipts, {})

    def graph_review(self):
        source, plan, preview = graph_publication_fixture()
        self.source, self.source_plan, self.preview = source, plan, preview
        admin = preview['context']
        def envelope(reply):
            path = self.calls[-1][1]
            if path.endswith('/publication-preview'):
                return dict(context=admin, preview=deepcopy(self.preview))
            if path == '/api/v1/dbt/runs/' + ID:
                return dict(context=admin, run=deepcopy(self.source))
            if path == '/api/v1/dbt/plans/' + PLAN:
                return dict(context=admin, plan=deepcopy(self.source_plan))
            return reply
        self.envelope = envelope
        self.corrupt = lambda r: r.update(origin='native_graph') if r['kind'] == 'publication' else None
        path = Path(self.temp.name) / 'graph-review.json'
        path.write_text(json.dumps(preview))
        return admin, path

    def test_graph_publication_preview_confirm_and_original_recovery(self):
        admin, path = self.graph_review()
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders']
        reply = self.call('publish-preview', *flags, context=admin)
        self.assertEqual(reply['preview'], self.preview)
        self.assertEqual([c[0] for c in self.calls], ['GET', 'GET', 'GET', 'GET'])
        self.assertTrue(all('Idempotency-Key' not in c[4] for c in self.calls))
        flags += ['--expected-target-file=' + str(path), '--idempotency-key=graph-original']
        self.drop = True
        result = self.call('publish', *flags, context=admin)
        self.assertTrue(result['request_uncertain'])
        original = deepcopy(self.calls[-1])
        self.assertEqual(result['retry']['body'], dict(context=admin, node_id='model.lake.orders', expected_target=self.preview['expected_target']))
        self.assertEqual(set(result['retry']['body']), {'context', 'node_id', 'expected_target'})
        self.source['models'][1]['publication_id'] = PUB
        self.source['publication_allowed'] = False
        self.preview['expected_target']['destination']['physical_name'] = 'changed-after-review'
        reply = self.call('publish', *flags, context=admin)
        self.assertEqual(reply['publication']['origin'], 'native_graph')
        self.assertEqual(self.calls[-1], original)
        self.assertEqual(len(self.receipts), 1)
        self.assertEqual(reply['publication']['expected_target']['destination']['physical_name'], 'orders')

    def test_graph_publication_preview_source_scope_quality_and_task_tampers_blocked(self):
        admin, path = self.graph_review()
        good_source, good_plan, good_preview = deepcopy(self.source), deepcopy(self.source_plan), deepcopy(self.preview)
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders', '--expected-target-file=' + str(path)]
        preview_changes = [lambda v: v.update(origin='native_table'), lambda v: v.update(origin='sql_wap'),
            lambda v: v.update(run_id=PLAN), lambda v: v.update(plan_id=ID), lambda v: v.update(node_id='model.lake.second'),
            lambda v: v.update(source_digest='wrong'), lambda v: v.update(source_actor='other'),
            lambda v: v['source_context'].update(grant_revision=999), lambda v: v['candidate']['ref'].update(object_id='other'),
            lambda v: v['quality'].update(count=3), lambda v: v['quality']['test_ids'].pop(),
            lambda v: v['target'].update(catalog='forged'), lambda v: v['target'].update(schema='raw'), lambda v: v['target'].update(name='second'),
            lambda v: v['expected_target']['destination'].update(schema_id='raw'),
            lambda v: v['expected_target']['destination'].update(physical_name='second')]
        source_changes = [lambda r: r.update(executor='native_table'), lambda r: r.update(publication_allowed=False),
            lambda r: r.update(native_verified=False), lambda r: r.update(native_tests=False), lambda r: r.update(tests_status='NOT_RUN'),
            lambda r: r['tests'].pop(), lambda r: r['tests'][1].update(state='FAILED', failure_count=1),
            lambda r: r['tests'][2].update(failure_count=False), lambda r: r['models'].pop(),
            lambda r: r['models'][2].update(state='FAILED'), lambda r: r['models'][0].update(state='NATIVE_VERIFIED'),
            lambda r: r['models'][1]['native_receipt']['candidate'].update(snapshot='wrong'),
            lambda r: r['models'][1].update(operation_ids=['other']), lambda r: r['operations'][0].update(node_id='other'),
            lambda r: r.pop('task_receipts'), lambda r: r['task_receipts']['build'].update(success=False),
            lambda r: r['task_receipts']['test'].update(completed=False),
            lambda r: r['task_receipts']['test']['selected'].pop(),
            lambda r: r['task_receipts']['build']['results'].pop(),
            lambda r: r['operations'].append(dict(id='extra', phase='build', node_id='model.lake.stage', state='FINISHED', committed=True))]
        for change in preview_changes:
            self.preview = deepcopy(good_preview); change(self.preview); path.write_text(json.dumps(self.preview))
            before = sum(c[0] == 'POST' for c in self.calls)
            with self.subTest(preview=change), self.assertRaises((ValueError, StopIteration)):
                self.call('publish-preview', '--run-id=' + ID, '--node-id=model.lake.orders', context=admin)
            self.assertEqual(sum(c[0] == 'POST' for c in self.calls), before)
        self.preview = deepcopy(good_preview); path.write_text(json.dumps(self.preview))
        for change in source_changes:
            self.source = deepcopy(good_source); change(self.source)
            before = sum(c[0] == 'POST' for c in self.calls)
            with self.subTest(source=change), self.assertRaises((ValueError, StopIteration)):
                self.call('publish-preview', '--run-id=' + ID, '--node-id=model.lake.orders', context=admin)
            self.assertEqual(sum(c[0] == 'POST' for c in self.calls), before)
        self.source = good_source
        self.source_plan = deepcopy(good_plan); self.source_plan['models'][2]['materialized'] = 'ephemeral'
        with self.assertRaises(ValueError):
            self.call('publish-preview', '--run-id=' + ID, '--node-id=model.lake.orders', context=admin)

    def test_graph_publication_ack_mismatch_retains_frozen_origin_and_intent(self):
        admin, path = self.graph_review()
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders', '--expected-target-file=' + str(path), '--idempotency-key=graph-ack']
        changes = [dict(origin='native_table'), dict(origin='sql_wap'), dict(run_id=PLAN), dict(parent_run_id=PLAN),
                   dict(plan_id=ID), dict(node_id='model.lake.second'), dict(expected_target={}), dict(context=C)]
        for change in changes:
            self.corrupt = lambda r, c=change: r.update(origin='native_graph', **{k:v for k,v in c.items() if k != 'origin'}) if 'origin' not in c else r.update(c)
            reply = self.call('publish', *flags, context=admin)
            self.assertTrue(reply['request_uncertain'])
            self.assertEqual(reply['retry']['idempotency_key'], 'graph-ack')
            self.assertEqual(reply['retry']['body'], self.calls[-1][3])
            self.assertEqual(reply['retry']['body']['expected_target'], self.preview['expected_target'])

    def test_graph_sandbox_relation_marts_preview_and_forged_saved_relation(self):
        admin, _ = self.graph_review()
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders']
        self.assertEqual(self.source_plan['models'][1]['relation'], ['sales', 'sandbox', 'orders'])
        self.assertNotIn('graph_task_receipts', self.source)
        self.assertEqual(self.call('publish-preview', *flags, context=admin)['preview']['target'],
                         dict(catalog='sales', schema='marts', name='orders'))
        good = deepcopy(self.source_plan)
        for index, forged in ((0, 'other_catalog'), (2, 'other_alias')):
            self.source_plan = deepcopy(good)
            self.source_plan['models'][1]['relation'][index] = forged
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.call('publish-preview', *flags, context=admin)
        self.assertTrue(all(c[0] == 'GET' for c in self.calls))

    def test_frozen_graph_replay_after_source_cancellation_quality_or_other_publication(self):
        changes = [lambda r: r.update(state='CANCELED', cancel_requested=True, publication_allowed=False),
                   lambda r: r.update(state='FAILED', tests_status='FAILED', publication_allowed=False),
                   lambda r: r['task_receipts']['test'].update(success=False),
                   lambda r: r['models'][1]['candidate'].update(snapshot='invalidated'),
                   lambda r: r['models'][2].update(publication_id=PUB)]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                admin, path = self.graph_review()
                flags = ['--run-id=' + ID, '--node-id=model.lake.orders',
                         '--expected-target-file=' + str(path), '--idempotency-key=replay-' + str(index)]
                before = len(self.calls)
                self.drop = True
                uncertain = self.call('publish', *flags, context=admin)
                self.assertTrue(uncertain['request_uncertain'])
                original = deepcopy(self.calls[-1])
                change(self.source)
                recovered = self.call('publish', *flags, context=admin)
                self.assertEqual(recovered['publication']['id'], PUB)
                self.assertEqual(self.calls[-1], original)
                self.assertEqual([c[0] for c in self.calls[before:]], ['POST', 'POST'])
                self.assertEqual(len(self.receipts), index + 1)
                with self.assertRaises(ValueError):
                    # The read-only gate still rejects current invalid proof.
                    if index == 4:
                        self.source['publication_allowed'] = False
                    self.call('publish-preview', '--run-id=' + ID, '--node-id=model.lake.orders', context=admin)

    def test_complete_frozen_review_validation_and_b5_publish_without_get(self):
        admin, path = self.graph_review()
        flags = ['--run-id=' + ID, '--node-id=model.lake.orders', '--expected-target-file=' + str(path)]
        good = deepcopy(self.preview)
        for change in (lambda v: v.update(publisher_actor='other'), lambda v: v.pop('source_actor'),
                       lambda v: v.pop('source_digest'), lambda v: v['source_context'].update(database_id='other'),
                       lambda v: v['candidate']['ref'].update(schema_id='raw'),
                       lambda v: v['target'].update(catalog='other'),
                       lambda v: v['target'].update(name='other')):
            bad = deepcopy(good); change(bad); path.write_text(json.dumps(bad))
            before = len(self.calls)
            with self.assertRaises(ValueError):
                self.call('publish', *flags, context=admin)
            self.assertEqual(len(self.calls), before)
        good['origin'] = 'native_table'
        path.write_text(json.dumps(good))
        self.corrupt = lambda r: r.update(origin='native_table')
        before = len(self.calls)
        reply = self.call('publish', *flags, context=admin)
        self.assertEqual(reply['publication']['origin'], 'native_table')
        self.assertEqual([c[0] for c in self.calls[before:]], ['POST'])
        self.assertEqual(self.calls[-1][3], dict(context=admin, node_id=good['node_id'], expected_target=good['expected_target']))

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
            before = len(self.calls)
            result = self.call('publish', *flags, context=admin)
            self.assertEqual([c[0] for c in self.calls[before:]], ['POST'])
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
