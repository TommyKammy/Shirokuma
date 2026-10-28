"""Strict Stage HTTP framing on Workbench8888 and independent native8080."""
from dataclasses import asdict
from contextlib import contextmanager
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
import io
import json
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import test_db_stages as fixtures
import db_stages as stages
import db_stage_scans as scans
import runtime_profile
import warehouse_resident as warehouse
import workbench_server as wb
import database_inventory as inventory


class Connection:
    def __init__(self, incoming):
        self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()

    def makefile(self, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.write(data)


class StageHTTPTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.StageStateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.c = asdict(self.f.c)
        self.native = False
        self.loopback = False
        for p in (patch.object(runtime_profile, 'active', return_value=True),
                  patch.object(wb.WorkbenchHandler, 'log_message')):
            p.start()
            self.addCleanup(p.stop)

    def request(self, path='/api/v1/stages', body=None, key=None, context=True, headers=(), method=None,
                identity='owner', role=None, request_headers=None):
        if body is None and context:
            path += '?' + urlencode({'context': json.dumps(self.c)})
        values = list(self.f.b.fixture.headers(identity).items()) if identity else []
        values += [('X-Shirokuma-Role', role or self.c.get('selected_role', 'admin')),
                   ('Host', '127.0.0.1'), ('Connection', 'close')]
        raw = b'' if body is None else json.dumps(body).encode()
        values.append(('Content-Length', str(len(raw))))
        if body is not None:
            values.append(('Content-Type', 'application/json'))
        if key is not None:
            values.append(('Idempotency-Key', key))
        values.extend(headers)
        if request_headers is not None:
            values = list(request_headers) + [('Host', '127.0.0.1'), ('Connection', 'close'),
                                             ('Content-Length', str(len(raw)))]
        request = ((method or ('GET' if body is None else 'POST')) + ' ' + path + ' HTTP/1.1\r\n' +
            ''.join(k + ': ' + v + '\r\n' for k, v in values) + '\r\n').encode() + raw
        connection = Connection(request)
        if self.native:
            # Capture the real native Handler class and exercise its HTTP parser;
            # no listening socket or runtime configuration is created.
            loopback = self.loopback
            class Server:
                def __init__(self, address, handler):
                    self.handler = handler
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def handle_request(self):
                    if not loopback:
                        self.handler(connection, ('127.0.0.1', 1), SimpleNamespace())
                        return
                    with ThreadingHTTPServer(('127.0.0.1', 0), self.handler) as server:
                        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
                        thread.start()
                        try:
                            with socket.create_connection(server.server_address, timeout=3) as client:
                                client.sendall(request)
                                while True:
                                    data = client.recv(65536)
                                    if not data:
                                        break
                                    connection.outgoing.write(data)
                        finally:
                            server.shutdown()
                            thread.join(3)
            with patch.object(warehouse.http.server, 'ThreadingHTTPServer', Server):
                warehouse.serve_endpoint(port=8080, warehouse='LOCAL', once=True)
        else:
            wb.WorkbenchHandler(connection, ('127.0.0.1', 1), SimpleNamespace())
        response = HTTPResponse(Connection(connection.outgoing.getvalue()))
        response.begin()
        raw = response.read()
        return response.status, json.loads(raw) if 'json' in response.getheader('Content-Type', '') else raw

    def save(self, key='save', **changes):
        return self.request(body=dict(context=self.c, expected_revision=0,
            definition=self.f.definition(), **changes), key=key)

    def test_scan_routes_replay_strict_fields_retry_cas_and_envelopes(self):
        target = self.f.b.existing('scan_route_target')
        definition = self.f.definition(mode='append', target=asdict(target), atomic_append=True)
        definition.pop('destination')
        stage = self.f.save(definition=definition)
        path = '/api/v1/stages/' + stage['id'] + '/scans'
        body = dict(context=self.c, expected_revision=1)
        with patch.object(scans, '_start'), patch.object(scans, '_start_retry'):
            status, response = self.request(path, body=body, key='scan')
            self.assertEqual(status, 202, response)
            self.assertEqual(response['context'], self.c)
            scan = response['scan']
            self.f.path.unlink()
            self.assertEqual(self.request(path, body=body, key='scan'), (202, response))
            self.assertEqual(self.request(path, body=dict(body, file='x.csv'), key='extra')[0], 400)
            self.assertEqual(self.request(path, body=dict(body, expected_revision=2), key='scan')[0], 409)
            status, listing = self.request(path)
            self.assertEqual(status, 200, listing)
            self.assertEqual(listing['scans'][0]['id'], scan['id'])
            inspect_path = path + '/' + scan['id']
            self.assertEqual(self.request(inspect_path)[1]['scan'], scan)
            retry = dict(body, item_id=scan['items'][0]['id'], expected_attempt=0)
            self.assertEqual(self.request(inspect_path + '/retry', body=retry, key='retry')[0], 409)
            self.assertEqual(self.request(inspect_path + '/retry', body=dict(retry, unexpected=True), key='bad')[0], 400)
            self.assertEqual(self.request(inspect_path + '/cancel', body=body, key='cancel')[0], 200)
            scans._execute(scan['id'])
            self.assertEqual(self.request(inspect_path)[1]['scan']['status'], 'canceled')

    def test_scan_retry_route_attempt_cas_and_response_loss(self):
        target = self.f.b.existing('retry_route_target')
        definition = self.f.definition(mode='append', target=asdict(target), atomic_append=True)
        definition.pop('destination')
        stage = self.f.save(definition=definition)
        self.f.path.write_bytes(b'id,label\n1,too,many\n')
        path = '/api/v1/stages/' + stage['id'] + '/scans'
        body = dict(context=self.c, expected_revision=1)
        with patch.object(scans, '_start'), patch.object(scans, '_start_retry'):
            status, response = self.request(path, body=body, key='scan')
            self.assertEqual(status, 202, response)
            scan = response['scan']
            scans._execute(scan['id'])
            scan = self.request(path + '/' + scan['id'])[1]['scan']
            item = scan['items'][0]
            retry_path = path + '/' + scan['id'] + '/retry'
            retry = dict(body, item_id=item['id'], expected_attempt=0)
            self.assertEqual(self.request(retry_path, body=retry, key='stale')[0], 409)
            retry['expected_attempt'] = 1
            accepted = self.request(retry_path, body=retry, key='retry')
            self.assertEqual(accepted[0], 202, accepted)
            self.assertEqual(self.request(retry_path, body=retry, key='retry'), accepted)
            retry_scan = accepted[1]['scan']
            self.assertNotEqual(retry_scan['id'], scan['id'])
            self.assertEqual(retry_scan['source_item_id'], item['id'])
            self.assertEqual(len(retry_scan['items']), 1)
            scans._execute(retry_scan['id'])
            replay = self.request(retry_path, body=retry, key='retry')
            self.assertEqual(replay[0], 202, replay)
            self.assertEqual(replay[1]['scan']['items'][0]['attempt'], 2)

    def test_fresh_context_duplicate_http_never_exposes_old_execution(self):
        target = self.f.b.existing('duplicate_route_target')
        definition = self.f.definition(mode='append', target=asdict(target), atomic_append=True)
        definition.pop('destination')
        stage = self.f.save(definition=definition)
        old = self.f.execute(self.f.submit(stage))
        self.f.revoke()
        self.f.regrant(new_generation=True)
        self.c = asdict(self.f.c)
        stage = self.f.save(key='refresh', stage_id=stage['id'], expected_revision=1, definition=definition)
        preview, file = self.f.preview(stage, key='fresh-preview')
        body = dict(context=self.c, expected_revision=2, file=file['name'], version=file['version'],
                    expected_preview_fingerprint=preview['confirmation_fingerprint'])
        status, response = self.request('/api/v1/stages/' + stage['id'] + '/imports', body=body, key='fresh-duplicate')
        self.assertEqual(status, 202, response)
        self.assertEqual(response['context'], self.c)
        self.assertEqual(response['run'], dict(id=None, status='skipped', committed=True, deduplicated=True, context=self.c))
        self.assertNotIn(old['operation_id'], json.dumps(response))
        self.assertNotIn(json.dumps(old['context']['generation_id']), json.dumps(response))

    def test_inventory_bootstrap_parity_ref_and_role_database_isolation(self):
        ref = self.f.b.existing(name='lookup')
        # Extend the existing fake catalog transport with its listing response;
        # inventory resolution and authorization remain real.
        catalog = self.f.b.catalog
        @contextmanager
        def listing(database, binding):
            with catalog(database, binding) as api:
                def get(path):
                    if path.split('?')[0].endswith('/tables'):
                        return {'identifiers': [dict(namespace=['raw'], name='lookup')]}
                    return api(path)
                yield get
        paths = ['/api/v1/databases', '/api/v1/databases/' + self.c['database_id'] + '/schemas',
                 '/api/v1/objects/resolve?' + urlencode(dict(database_id=ref.database_id,
                    schema_id=ref.schema_id, name='lookup'))]
        with patch.object(inventory, 'catalog_api', listing), patch.object(warehouse, '_native_request') as sql:
            for path in paths:
                self.native = False
                expected = self.request(path, context=False)
                self.assertEqual(expected[0], 200, expected)
                self.native = True
                self.assertEqual(self.request(path, context=False), expected)
            resolved = self.request(paths[-1], context=False)[1]['ref']
            self.assertEqual({k: resolved[k] for k in asdict(ref)}, asdict(ref))
            self.assertEqual(len(resolved), 6)
            other_database = next(d for d in self.f.b.databases if d != ref.database_id)
            cross = '/api/v1/objects/resolve?' + urlencode(dict(database_id=other_database,
                schema_id=ref.schema_id, name='lookup'))
            self.assertEqual(self.request(cross, context=False)[0], 404)
            for native in (False, True):
                self.native = native
                schemas = self.request(paths[1], context=False, role='bi_viewer')[1]['items']
                self.assertEqual([s['sql_name'] for s in schemas], ['marts'])
                self.assertEqual(self.request(paths[-1], context=False, role='bi_viewer')[0], 404)
                self.assertEqual(self.request(paths[1], context=False, identity='alice')[0], 404)
            self.f.revoke()
            for native in (False, True):
                self.native = native
                self.assertEqual(self.request(paths[1], context=False)[0], 404)
                self.assertNotIn(self.c['database_id'], [d['id'] for d in
                    self.request(paths[0], context=False)[1]['items']])
            sql.assert_not_called()
        self.assertFalse(self.f.b.calls)

    def test_inventory_strict_framing_auth_and_native_management_refusal(self):
        resolve = '/api/v1/objects/resolve?database_id=d&schema_id=s&name=n'
        for native in (False, True):
            self.native = native
            for path in ('/api/v1/databases?unknown=x', '/api/v1/databases?x=1&x=2',
                         '/api/v1/databases#fragment', resolve + '&name=other', resolve + '&unknown=x',
                         resolve.replace('name=n', 'name='), resolve.replace('&name=n', ''),
                         '/api/v1/databases/d/schemas?unknown=x'):
                self.assertEqual(self.request(path, context=False)[0], 400, path)
            for headers in ([('Content-Length', '0')], [('Transfer-Encoding', 'chunked')],
                            [('Transfer-Encoding', '')], [('X-Shirokuma-Role', 'admin')]):
                self.assertEqual(self.request('/api/v1/databases', context=False, headers=headers)[0], 400)
            self.assertEqual(self.request('/api/v1/databases', body={}, context=False, method='GET')[0], 400)
            for auth in ([], [('Authorization', 'Basic invalid')]):
                self.assertEqual(self.request('/api/v1/databases', context=False,
                    request_headers=auth + [('X-Shirokuma-Role', 'admin')])[0], 401)
            valid = list(self.f.b.fixture.headers('owner').items())
            for roles in ([], [('X-Shirokuma-Role', 'invalid')]):
                self.assertEqual(self.request('/api/v1/databases', context=False,
                    request_headers=valid + roles)[0], 400)
        self.native = True
        with patch.object(warehouse, '_native_request') as sql, patch('db_stage_routes.dispatch') as stage:
            for path in ('/api/v1/databases', '/api/v1/databases/d/schemas', '/api/v1/objects/resolve',
                         '/api/v1/databases/d/grants', '/api/v1/grants', '/api/v1/schemas/s/objects'):
                for method in ('POST', 'DELETE'):
                    self.assertEqual(self.request(path, context=False, method=method)[0], 404, path)
            for path in ('/api/v1/grants', '/api/v1/databases/d/grants', '/api/v1/schemas/s/objects'):
                self.assertEqual(self.request(path, context=False)[0], 404)
            sql.assert_not_called()
            stage.assert_not_called()
        self.assertFalse(self.f.b.calls)

    def test_workbench_save_files_preview_import_run_envelopes(self):
        status, result = self.save()
        self.assertEqual(status, 200, result)
        stage = result['stage']
        self.assertEqual(result['context'], self.c)
        self.assertEqual(self.request()[1]['stages'], [stage])
        path = '/api/v1/stages/' + stage['id']
        self.assertEqual(self.request(path)[1]['stage'], stage)
        files = self.request(path + '/files')[1]
        self.assertEqual(files['revision'], 1)
        body = dict(context=self.c, expected_revision=1, file='data.csv', version=files['files'][0]['version'])
        status, preview = self.request(path + '/preview', body, key='preview')
        self.assertEqual(status, 200, preview)
        body['expected_preview_fingerprint'] = preview['confirmation_fingerprint']
        status, accepted = self.request(path + '/imports', body, key='import')
        self.assertEqual(status, 202, accepted)
        self.assertEqual(accepted['stage_id'], stage['id'])
        run = self.f.execute(accepted['run'])
        show = path + '/runs/' + run['id']
        self.assertEqual(self.request(show)[1]['run']['status'], 'succeeded')
        self.assertEqual(self.request(path + '/runs')[1]['runs'][0]['id'], run['id'])
        self.assertEqual(self.request(show + '/cancel', dict(context=self.c), key='cancel')[0], 200)
        self.f.path.unlink()
        self.assertEqual(self.request(path + '/imports', body, key='import')[1]['run']['id'], run['id'])

    def test_full9_and_strict_routes_mutation_keys_all_actions(self):
        stage = self.save()[1]['stage']
        path = '/api/v1/stages/' + stage['id']
        for name in self.c:
            saved, self.c = self.c, {k: v for k, v in self.c.items() if k != name}
            self.assertEqual(self.request()[0], 400)
            self.c = saved
        self.assertEqual(self.request(context=False)[0], 400)
        self.assertEqual(self.request('/api/v1/stages?context={}&context={}', context=False)[0], 400)
        self.assertEqual(self.save(key=None)[0], 400)
        self.assertEqual(self.request(body=dict(context=self.c), key='a', headers=[('Idempotency-Key', 'b')])[0], 400)
        self.assertEqual(self.save(owner='override')[0], 400)
        for action in ('preview', 'imports', 'exports', 'runs/00000000-0000-0000-0000-000000000001/cancel'):
            self.assertEqual(self.request(path + '/' + action, dict(context=self.c))[0], 400)
        for route in (path + '/scan', path + '/schedule', path + '/runs/nope',
                      '/api/v1/stages/runs/00000000-0000-0000-0000-000000000001', path + '/runs/00000000-0000-0000-0000-000000000001/reconcile'):
            self.assertEqual(self.request(route)[0], 404, route)
        self.assertEqual(self.request(headers=[('Transfer-Encoding', 'chunked')])[0], 400)
        self.assertEqual(self.request(headers=[('Content-Length', '0')])[0], 400)
        self.assertEqual(self.request(body=dict(context=self.c), key='origin', headers=[('Origin', 'https://invalid')])[0], 403)
        self.assertFalse(self.f.b.calls)

    def test_native8080_dispatches_stages_independently_preserves_sql_and_unknown_gate(self):
        self.native = True
        with patch.object(warehouse, '_native_request', side_effect=AssertionError('Stage routed through native SQL')):
            status, result = self.save()
            self.assertEqual(status, 200, result)
            stage = result['stage']
            self.assertEqual(self.request()[1]['stages'], [stage])
            self.assertEqual(self.request('/api/v1/stages/' + stage['id'] + '/files')[0], 200)
            self.assertEqual(self.save(owner='override')[0], 400)
            self.assertEqual(self.request(headers=[('Transfer-Encoding', 'chunked')])[0], 400)
        native = SimpleNamespace(sql_endpoint_url='http://127.0.0.1:8080', mode='database')
        with patch.object(runtime_profile, 'current', return_value=native):
            self.assertEqual(self.request('/api/v1/not-a-route', context=False)[0], 404)
            with patch.object(warehouse, '_native_request', return_value={'id': 'unchanged'}) as sql:
                self.assertEqual(self.request('/v1/statement', body={'sql': 'SELECT 1'}, context=False)[1], {'id': 'unchanged'})
                self.assertEqual(sql.call_args.args[1:3], ('/v1/statement', 'POST'))
        self.assertFalse(self.f.b.calls)

    def test_revoke_cancel_replay_minimal(self):
        stage = self.save()[1]['stage']
        run = self.f.submit(stage)
        show = '/api/v1/stages/' + stage['id'] + '/runs/' + run['id']
        self.f.revoke()
        self.assertEqual(self.request(show)[0], 403)
        status, observation = self.request(show + '/cancel', dict(context=self.c), key='cancel')
        self.assertEqual(status, 200, observation)
        self.assertEqual(set(observation['run']), {'id', 'status'})
        self.assertEqual(self.request(show + '/cancel', dict(context=self.c), key='cancel')[0], 200)
        self.assertNotIn('summary', observation['run'])

    def test_retiring_run_and_scan_cancel_are_exact_owned_and_minimal(self):
        target = self.f.b.existing('cancel_target')
        definition = self.f.definition(mode='append', target=asdict(target), atomic_append=True)
        definition.pop('destination')
        stage = self.f.save(definition=definition)
        run = self.f.submit(stage)
        scan_path = '/api/v1/stages/' + stage['id'] + '/scans'
        with patch.object(scans, '_start'):
            scan = self.request(scan_path, dict(context=self.c, expected_revision=1), key='scan')[1]['scan']
        self.addCleanup(lambda: scans._live.pop(scan['id'])[-1].close() if scan['id'] in scans._live else None)
        paths = [('/api/v1/stages/' + stage['id'] + '/runs/' + run['id'], 'run', {}),
                 (scan_path + '/' + scan['id'], 'scan', dict(expected_revision=1))]
        self.f.b.retire()
        for path, kind, extra in paths:
            for name, value in self.c.items():
                changed = dict(self.c, **{name: value + 1 if type(value) is int else str(value) + '-wrong'})
                self.assertGreaterEqual(self.request(path + '/cancel', dict(context=changed, **extra), key='bad-' + name)[0], 400, name)
            self.assertEqual(self.request(path + '/cancel', dict(context=self.c, **extra),
                key='foreign', identity='alice')[0], 404)
            self.assertGreaterEqual(self.request(path)[0], 400)
            status, result = self.request(path + '/cancel', dict(context=self.c, **extra), key='cancel-' + kind)
            self.assertEqual(status, 200, result)
            self.assertEqual(set(result[kind]), {'id', 'status'})
        self.assertGreaterEqual(self.request(scan_path, dict(context=self.c, expected_revision=1), key='denied')[0], 400)
        del self.f.b.fixture.users['owner']
        self.f.b.fixture.save()
        for path, _, extra in paths:
            self.assertIn(self.request(path + '/cancel', dict(context=self.c, **extra), key='removed')[0], (401, 403))
        self.assertFalse(self.f.b.calls)

    def test_native_handler_real_loopback_full_context_and_strict_framing(self):
        self.native = self.loopback = True
        status, result = self.save()
        self.assertEqual(status, 200, result)
        stage = result['stage']
        path = '/api/v1/stages/' + stage['id']
        files = self.request(path + '/files')[1]
        body = dict(context=self.c, expected_revision=1, file='data.csv', version=files['files'][0]['version'])
        preview = self.request(path + '/preview', body, key='loopback-preview')[1]
        body['expected_preview_fingerprint'] = preview['confirmation_fingerprint']
        accepted_status, accepted = self.request(path + '/imports', body, key='loopback-import')
        self.assertEqual(accepted_status, 202, accepted)
        self.assertEqual(accepted['context'], self.c)
        self.assertEqual(self.f.execute(accepted['run'])['status'], 'succeeded')
        self.assertEqual(self.request(path + '/runs/' + accepted['run']['id'])[1]['run']['status'], 'succeeded')
        cases = [dict(context=False), dict(headers=[('Content-Length', '0')]),
                 dict(headers=[('Transfer-Encoding', 'chunked')]),
                 dict(headers=[('X-Shirokuma-Role', 'admin')])]
        for options in cases:
            self.assertEqual(self.request(**options)[0], 400, options)
        self.assertEqual(self.request('/api/v1/stages?context={}&context={}', context=False)[0], 400)
        save = dict(context=self.c, expected_revision=0, definition=self.f.definition())
        for headers in ([('Idempotency-Key', 'duplicate')], [('Content-Type', 'application/json')], [('Content-Length', '0')]):
            self.assertEqual(self.request(body=save, key='framing', headers=headers)[0], 400, headers)

    def test_fresh_markers_and_malformed_replay_are_strict_on_both_handlers(self):
        stage = self.save()[1]['stage']
        path = '/api/v1/stages/' + stage['id']
        version = stages._hash(self.f.path.read_bytes())
        for native in (False, True):
            self.native = native
            self.loopback = native
            body = dict(context=self.c, expected_revision=[], file='data.csv', version=version,
                        expected_preview_fingerprint='x')
            status, result = self.request(path + '/imports', body, key='wrong-types-' + str(native))
            self.assertEqual((status, result['code']), (400, 'STAGE_INPUT_REJECTED'))
            self.assertEqual(result['details'], dict(admission='NOT_ATTEMPTED', reason='INVALID_INPUT'))
            export = dict(context=self.c, expected_revision=1, source=[], format='csv', file='export.csv')
            status, result = self.request(path + '/exports', export, key='wrong-ref-' + str(native))
            self.assertEqual((status, result['code']), (400, 'STAGE_INPUT_REJECTED'))
            self.assertEqual(result['details']['reason'], 'INVALID_INPUT')
        self.native = self.loopback = False
        preview = self.request(path + '/preview', dict(context=self.c, expected_revision=1, file='data.csv', version=version), key='preview')[1]
        body = dict(context=self.c, expected_revision=1, file='data.csv', version=version,
                    expected_preview_fingerprint=preview['confirmation_fingerprint'])
        accepted = self.request(path + '/imports', body, key='accepted')[1]['run']
        self.f.path.unlink()
        for native in (False, True):
            self.native = self.loopback = native
            self.assertEqual(self.request(path + '/imports', body, key='accepted')[1]['run']['id'], accepted['id'])
            status, replay = self.request(path + '/imports', dict(body, expected_revision=[]), key='accepted')
            self.assertEqual((status, replay['code']), (409, 'IDEMPOTENCY_CONFLICT'))
            self.assertNotIn('admission', replay['details'])

    def test_native_loopback_get_recovers_postlink_loss_and_downloads_receipt(self):
        self.f.doCleanups()
        self.f = fixtures.StageExportTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.c = asdict(self.f.c)
        self.native = self.loopback = True
        stage = self.save()[1]['stage']
        path = '/api/v1/stages/' + stage['id']
        status, accepted = self.request(path + '/exports', dict(context=self.c, expected_revision=1,
            source=asdict(self.f.source), format='csv', file='http.csv'), key='http-export')
        self.assertEqual(status, 202, accepted)
        run = accepted['run']
        with patch.object(stages, '_publication_ledger', side_effect=OSError('ledger lost')):
            stages._execute_run(run['id'], stages.ds.Execution(self.f.p, run['operation_id']))
        status, observation = self.request(path + '/runs/' + run['id'])
        self.assertEqual(status, 200, observation)
        self.assertEqual(observation['run']['status'], 'succeeded')
        self.assertEqual(observation['run']['operation']['state'], 'FINISHED')
        export_id = observation['run']['export_id']
        receipt = self.request(path + '/exports/' + export_id)[1]['receipt']
        self.assertEqual(receipt['expected_count'], receipt['written_count'])
        self.assertNotIn('row_count', receipt)
        status, content = self.request(path + '/exports/' + export_id + '/content')
        self.assertEqual(status, 200)
        self.assertEqual(content, (self.f.directory / 'exports' / 'http.csv').read_bytes())
        self.assertEqual(len(self.f.f.posts), 2)


if __name__ == '__main__':
    unittest.main()
