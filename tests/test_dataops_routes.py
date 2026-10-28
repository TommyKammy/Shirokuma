"""Real HTTP serialization around authenticated dispatcher; no engine access."""
import base64
from dataclasses import asdict
from http.client import HTTPResponse
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import workbench_server as wb
import dataops_routes as routes
import data_management as registry
import manual_ingest
import request_auth
import runtime_profile
from query_context import ResolvedQueryContext, QueryError

C = ResolvedQueryContext('db', 'sc', 'LOCAL', 'sales', 'raw', 'data_engineer', 1, 'gen', 3)
AUTHENTICATE = request_auth.authenticate_principal


class HTTPRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.principal = request_auth.AuthenticatedPrincipal('alice', 'data_engineer', False)
        for target, name, value in ((runtime_profile, 'active', True),
                                    (request_auth, 'authenticate_principal', self.principal), (routes, 'resolve_context', C)):
            p = patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def request(self, path, body=None, role='data_engineer', key=None, extra=None):
        headers = {'Authorization': 'Basic synthetic', 'X-Shirokuma-Role': role}
        if key:
            headers['Idempotency-Key'] = key
        if body is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        raw = b'' if body is None else json.dumps(body).encode()
        headers.update({'Host': '127.0.0.1', 'Connection': 'close', 'Content-Length': str(len(raw))})
        request = (('GET' if body is None else 'POST') + ' ' + path + ' HTTP/1.1\r\n' +
            ''.join(k + ': ' + v + '\r\n' for k, v in headers.items()) + '\r\n').encode() + raw
        class Connection:
            def __init__(self, incoming):
                self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args):
                return self.incoming
            def sendall(self, data):
                self.outgoing.write(data)
        connection = Connection(request)
        with patch.object(wb.WorkbenchHandler, 'log_message'):
            wb.WorkbenchHandler(connection, ('127.0.0.1', 1), SimpleNamespace())
        response = HTTPResponse(Connection(connection.outgoing.getvalue()))
        response.begin()
        return response.status, json.loads(response.read())

    def upload(self):
        return dict(context=asdict(C), mode='create', destination=dict(database_id='db', schema_id='sc', physical_name='orders'),
            filename='orders.csv', content_base64=base64.b64encode(b'id,amount\n001,12345678901234567890.123\n').decode())

    def test_preview_precision_and_confirmation(self):
        body = self.upload()
        status, result = self.request('/api/v1/ingests/preview', body)
        self.assertEqual(status, 200)
        self.assertEqual(result['context'], asdict(C))
        self.assertEqual(result['preview']['rows'], [['001', '12345678901234567890.123']])
        body['expected_preview_fingerprint'] = result['preview']['request_fingerprint']
        body['content_base64'] = base64.b64encode(b'id,amount\n002,1\n').decode()
        with patch.object(routes.manual_ingest, 'ingest') as ingest:
            self.assertEqual(self.request('/api/v1/ingests', body, key='one')[1]['code'], 'PREVIEW_CHANGED')
            ingest.assert_not_called()

    def test_real_basic_authentication_before_dispatch(self):
        salt = '12' * 16
        account = dict(role='data_engineer', platform_admin=False, salt=salt,
            password_hash=request_auth.password_hash('local-password', salt))
        with patch.object(request_auth, 'authenticate_principal', AUTHENTICATE), \
                patch.object(request_auth, '_principal_accounts', return_value={'alice': account}):
            self.assertEqual(self.request('/api/v1/ingests/preview', self.upload())[0], 401)
            basic = 'Basic ' + base64.b64encode(b'alice:local-password').decode()
            self.assertEqual(self.request('/api/v1/ingests/preview', self.upload(), extra={'Authorization': basic})[0], 200)

    def test_full_context_and_strict_queries(self):
        with patch.object(routes.manual_ingest, 'input_preview') as preview:
            for field, value in [('sql_catalog', 'finance'), ('grant_revision', 2), ('generation_id', 'old'), ('binding_revision', 2)]:
                body = self.upload()
                body['context'][field] = value
                self.assertEqual(self.request('/api/v1/ingests/preview', body)[0], 409)
            body = self.upload()
            del body['context']['sql_schema']
            self.assertEqual(self.request('/api/v1/ingests/preview', body)[0], 400)
            self.assertEqual(self.request('/api/v1/ingests/preview', self.upload(), role='admin')[0], 400)
            preview.assert_not_called()
        query = urlencode(dict(context=json.dumps(asdict(C))))
        for suffix in ('&extra=x', '&' + query):
            self.assertEqual(self.request('/api/v1/wap/models?' + query + suffix)[0], 400)
        duplicate = json.dumps(asdict(C))[:-1] + ', "database_id":"other"}'
        self.assertEqual(self.request('/api/v1/wap/models?' + urlencode({'context': duplicate}))[0], 400)

    def test_wap_dispatch_models_actions_and_metadata(self):
        ref = dict(database_id='db', schema_id='sc', object_id='obj', incarnation=1)
        body = dict(context=asdict(C), model_id='arbitrary', sql='SELECT * FROM src', sources=dict(src=ref),
            target=dict(database_id='db', schema_id='sc', physical_name='model'), expected_version=None)
        with patch.object(routes.manual_wap, 'save_model', return_value=dict(version=1)) as save:
            self.assertEqual(self.request('/api/v1/wap/models', body), (200, dict(context=asdict(C), model=dict(version=1))))
            self.assertEqual(save.call_args.kwargs['sources']['src'], registry.ResourceRef(**ref))
        operation = dict(id='op', state='FINISHED', result={})
        for action, identifiers in [('build', dict(model_id='arbitrary', version=1)), ('audit', dict(build_id='b')),
                                    ('publish', dict(build_id='b', audit_id='a')), ('rollback', dict(publication_id='p'))]:
            with patch.object(routes.manual_wap, action, return_value=operation) as run:
                self.assertEqual(self.request('/api/v1/wap/' + action, dict(context=asdict(C), **identifiers), key='once')[1]['operation'], operation)
                self.assertEqual(run.call_args.kwargs, dict(key='once', **identifiers))
        query = urlencode(dict(context=json.dumps(asdict(C))))
        with patch.object(routes.manual_wap, 'get_publication', return_value=dict(id='p')):
            self.assertEqual(self.request('/api/v1/wap/publications/p?' + query)[1]['publication'], dict(id='p'))

    def test_inspect_checks_saved_context_and_current_grants(self):
        execution = Mock()
        execution._row.return_value = dict(context=json.dumps(asdict(C)))
        query = urlencode(dict(context=json.dumps(asdict(C))))
        with patch.object(routes.ds, 'Execution', return_value=execution), \
                patch.object(routes, 'resolve_context', side_effect=QueryError('DB_ACCESS_REVOKED', 403)):
            self.assertEqual(self.request('/api/v1/dataops/op?' + query)[0], 403)
        execution.inspect.assert_not_called()
        changed = dict(asdict(C), sql_schema='sandbox')
        with patch.object(routes.ds, 'Execution', return_value=execution):
            self.assertEqual(self.request('/api/v1/dataops/op/cancel', dict(context=changed))[1]['code'], 'CONTEXT_MISMATCH')
        execution.cancel.assert_not_called()

    def test_submission_identity_and_ref_shape(self):
        body = self.upload()
        operation = dict(id='op', state='FINISHED', result={})
        with patch.object(routes.manual_ingest, 'ingest', return_value=operation) as ingest:
            self.assertEqual(self.request('/api/v1/ingests', body)[0], 400)
            status, reply = self.request('/api/v1/ingests', body, key='once')
            self.assertEqual((status, reply), (200, dict(context=asdict(C), operation=operation)))
            self.assertEqual(ingest.call_args.kwargs['destination'], registry.DestinationLocator('db', 'sc', 'orders'))
        ref = dict(database_id='db', schema_id='sc', object_id='obj', incarnation=1)
        self.assertEqual(routes.reference(ref), registry.ResourceRef(**ref))
        with self.assertRaises(ValueError):
            routes.reference(dict(ref, generation_id='gen'))

    def test_framing_origin_and_unknown_body(self):
        for extra in ({'Origin': 'http://other.invalid'}, {'Content-Type': 'text/plain'}):
            self.assertIn(self.request('/api/v1/ingests/preview', self.upload(), extra=extra)[0], (400, 403))
        self.assertEqual(self.request('/api/v1/ingests/preview', dict(self.upload(), file='/private/secret'))[0], 400)

    def test_cancel_uses_saved_owner_context_after_grant_loss(self):
        execution = Mock()
        execution._row.return_value = dict(context=json.dumps(asdict(C)), state='CANCELED', committed=0)
        execution.inspect.side_effect = QueryError('DB_ACCESS_REVOKED', 403)
        with patch.object(routes.ds, 'Execution', return_value=execution), \
                patch.object(routes.ds, '_cancel_account'), \
                patch.object(routes, 'current_context', side_effect=AssertionError('must not resolve grants')):
            status, reply = self.request('/api/v1/dataops/op/cancel', dict(context=asdict(C)))
        self.assertEqual(status, 200)
        self.assertEqual(reply['operation']['state'], 'CANCELED')
        execution.cancel.assert_called_once()
        execution._row.side_effect = QueryError('OPERATION_NOT_FOUND', 404)
        execution.cancel.reset_mock()
        with patch.object(routes.ds, 'Execution', return_value=execution):
            self.assertEqual(self.request('/api/v1/dataops/op/cancel', dict(context=asdict(C)))[0], 404)
        execution.cancel.assert_not_called()

    def test_preview_validates_beyond_sample_and_fingerprint_matches_core(self):
        options = dict(filename='x.csv', content=b'id\n' + b'1\n' * 51 + b'bad\n', mode='create',
            destination=registry.DestinationLocator('db', 'sc', 'x'), schema_hints={'id': 'BIGINT'})
        with self.assertRaises(ValueError):
            manual_ingest.input_preview(C, **options)
        options.update(content=b'id\n1\n')
        preview = manual_ingest.input_preview(C, **options)
        self.assertEqual(preview['total_rows'], 1)
        options['batch_size'] = 2
        self.assertNotEqual(preview['request_fingerprint'], manual_ingest.input_preview(C, **options)['request_fingerprint'])


class CoreHTTPTests(unittest.TestCase):
    setUpClass = classmethod(HTTPRoutesTests.setUpClass.__func__)
    tearDownClass = classmethod(HTTPRoutesTests.tearDownClass.__func__)

    def setUp(self):
        from test_manual_ingest import ManualIngestTests
        self.fixture = ManualIngestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.boundary = self.fixture.fixture
        self.context = self.boundary.context()
        self.active = patch.object(runtime_profile, 'active', return_value=True)
        self.active.start()
        self.addCleanup(self.active.stop)

    def request(self, path, body=None, *, user='alice', role='data_engineer', key=None):
        basic = 'Basic ' + base64.b64encode((user + ':correct').encode()).decode()
        return HTTPRoutesTests.request(self, path, body, role=role, key=key, extra={'Authorization': basic})

    def upload(self):
        return dict(context=asdict(self.context), mode='create', destination=asdict(self.boundary.locator('normal_http')),
            filename='data.csv', content_base64=base64.b64encode(b'id,label\n1,001\n2,\n').decode())

    def test_retiring_cancel_requires_exact_owned_context_and_current_account(self):
        import query_service as qs
        execution, query_id = self.running_operation()
        self.boundary.retire()
        path = '/api/v1/dataops/' + execution.id
        context = asdict(self.context)
        with patch.object(qs, '_transport', return_value={}) as transport:
            for name, value in context.items():
                changed = dict(context, **{name: value + 1 if type(value) is int else str(value) + '-wrong'})
                self.assertGreaterEqual(self.request(path + '/cancel', dict(context=changed))[0], 400, name)
            self.assertEqual(self.request(path + '/cancel', dict(context=context), user='owner')[0], 404)
            transport.assert_not_called()
            self.assertGreaterEqual(self.request(path + '?' + urlencode(dict(context=json.dumps(context))))[0], 400)
            self.assertGreaterEqual(self.request('/api/v1/ingests', self.upload(), key='denied')[0], 400)
            status, result = self.request(path + '/cancel', dict(context=context))
            self.assertEqual(status, 200, result)
            self.assertEqual(set(result['operation']), {'id', 'state'})
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(transport.call_args.args[1], 'DELETE')
            self.assertEqual(transport.call_args.args[2].headers['Authorization'], 'Basic original-secret')
            self.assertEqual(qs._row(query_id)['state'], 'CANCELED')
            del self.boundary.fixture.users['alice']
            self.boundary.fixture.save()
            self.assertIn(self.request(path + '/cancel', dict(context=context))[0], (401, 403))
            self.assertEqual(transport.call_count, 1)

    def test_real_auth_core_ledger_preview_replay_and_revocation(self):
        body = self.upload()
        status, preview = self.request('/api/v1/ingests/preview', body)
        self.assertEqual(status, 200)
        self.assertFalse(self.boundary.calls)
        body['expected_preview_fingerprint'] = preview['preview']['request_fingerprint']
        status, reply = self.request('/api/v1/ingests', body, key='once')
        self.assertEqual(status, 200, reply)
        operation = reply['operation']
        self.assertEqual(operation['state'], 'FINISHED')
        self.assertEqual(operation['result']['request_fingerprint'], body['expected_preview_fingerprint'])
        count = len(self.boundary.calls)
        self.assertTrue(self.request('/api/v1/ingests', body, key='once')[1]['operation']['replayed'])
        self.assertEqual(len(self.boundary.calls), count)
        path = '/api/v1/dataops/' + operation['id']
        query = urlencode(dict(context=json.dumps(asdict(self.context))))
        self.assertEqual(self.request(path + '?' + query)[0], 200)
        self.assertEqual(self.request(path + '?' + query, user='owner')[0], 404)
        self.assertEqual(self.request(path + '/cancel', dict(context=asdict(self.context)), user='owner')[0], 404)
        with routes.control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?', ('alice', self.context.database_id))
        self.assertEqual(self.request(path + '?' + query)[0], 403)
        status, cancelled = self.request(path + '/cancel', dict(context=asdict(self.context)))
        self.assertEqual(status, 200, cancelled)
        self.assertEqual(set(cancelled['operation']), {'id', 'state'})

    def test_view_export_rejected_before_sql_and_error_has_observation(self):
        view = self.boundary.existing('a_view', kind='VIEW')
        body = dict(context=asdict(self.context), source=asdict(view), format='csv')
        with patch.object(routes.manual_export, '_profile_ref', return_value='test-profile'):
            status, error = self.request('/api/v1/table-exports', body, key='view')
        self.assertEqual(status, 400, error)
        self.assertEqual(error['code'], 'VIEW_EXPORT_NOT_SUPPORTED')
        self.assertEqual(error['details']['operation']['state'], 'FAILED')
        self.assertFalse(self.boundary.calls)

    def running_operation(self):
        import query_service as qs
        execution = routes.ds.begin(self.boundary.principal, self.context,
            route='manual-route-test', idempotency_key='running', payload={})
        frozen, parent, _, _ = routes.ds._live[execution.id]
        with patch.object(qs, '_transport', return_value=dict(id='route_backend',
                nextUri='http://127.0.0.1:1234/v1/statement/executing/route_backend/1')):
            page = qs._submit_dataops(self.boundary.principal,
                routes.ds._Statement('select', 'SELECT 1'), frozen, parent, execution.id, 'running')
        return execution, page['id']

    def test_unknown_cancel_after_grant_loss_preserves_uncertainty_without_delete(self):
        import query_service as qs
        execution, query_id = self.running_operation()
        qs._state(query_id, 'UNKNOWN')
        with routes.control_store.connect() as db:
            db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (self.context.database_id,))
        with patch.object(qs, '_transport') as transport:
            status, error = self.request('/api/v1/dataops/' + execution.id + '/cancel',
                                         dict(context=asdict(self.context)))
        self.assertEqual((status, error['code']), (409, 'UNKNOWN_RECONCILIATION_REQUIRED'))
        self.assertEqual(error['details']['context'], asdict(self.context))
        self.assertEqual(error['details']['operation']['state'], 'UNKNOWN')
        self.assertEqual(qs._row(query_id)['state'], 'UNKNOWN')
        transport.assert_not_called()

    def test_unknown_arriving_after_cancel_precheck_is_not_deleted(self):
        import query_service as qs
        execution, query_id = self.running_operation()
        original = routes.ds.Execution.cancel
        def lose_before_cancel(current):
            qs._state(query_id, 'UNKNOWN')
            return original(current)
        with patch.object(routes.ds.Execution, 'cancel', lose_before_cancel), \
                patch.object(qs, '_transport', return_value={}) as transport:
            status, error = self.request('/api/v1/dataops/' + execution.id + '/cancel', dict(context=asdict(self.context)))
        self.assertEqual((status, error['code']), (409, 'UNKNOWN_RECONCILIATION_REQUIRED'))
        self.assertEqual(qs._row(query_id)['state'], 'UNKNOWN')
        transport.assert_not_called()

    def test_lost_delete_response_returns_unknown_recovery_without_inspect(self):
        import query_service as qs
        execution, query_id = self.running_operation()
        with routes.control_store.connect() as db:
            db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (self.context.database_id,))
        with patch.object(qs, '_transport', side_effect=OSError('lost DELETE response')) as transport, \
                patch.object(routes.ds.Execution, 'inspect', side_effect=AssertionError('must not inspect')):
            status, error = self.request('/api/v1/dataops/' + execution.id + '/cancel',
                                        dict(context=asdict(self.context)))
        self.assertEqual((status, error['code']), (409, 'UNKNOWN_RECONCILIATION_REQUIRED'))
        self.assertEqual(error['details']['context'], asdict(self.context))
        self.assertEqual(error['details']['operation']['state'], 'UNKNOWN')
        self.assertEqual(set(error['details']['operation']), {'id', 'state'})
        self.assertEqual(qs._row(query_id)['state'], 'UNKNOWN')
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.call_args.args[1], 'DELETE')
        with routes.control_store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM warehouse_admissions WHERE subject_id IN (?,?) AND state='UNKNOWN'",
                                        (execution.id, query_id)).fetchone()[0], 2)
        with patch.object(qs, '_transport') as replay_transport:
            self.assertEqual(self.request('/api/v1/dataops/' + execution.id + '/cancel',
                             dict(context=asdict(self.context)))[0], 409)
        replay_transport.assert_not_called()

    def test_replayed_changed_malformed_input_is_fingerprint_conflict_before_parse(self):
        body = self.upload()
        status, _ = self.request('/api/v1/ingests', body, key='once')
        self.assertEqual(status, 200)
        body['content_base64'] = base64.b64encode(b'bad\x00csv').decode()
        options = dict(content=b'bad\x00csv', filename=body['filename'], mode=body['mode'],
                       destination=routes.reference(body['destination'], locator=True))
        body['expected_preview_fingerprint'] = routes.manual_ingest.preview_fingerprint(self.context, **options)
        with patch.object(routes.manual_ingest, '_read', side_effect=AssertionError('must not parse')), \
                patch.object(routes.ds.queries, '_transport') as transport:
            status, error = self.request('/api/v1/ingests', body, key='once')
        self.assertEqual((status, error['code']), (409, 'IDEMPOTENCY_CONFLICT'))
        transport.assert_not_called()

    def test_accepted_oserror_returns_safe_authorized_recovery_record(self):
        body = self.upload()
        original = routes.manual_ingest.ingest
        def accepted_then_error(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('must never leak backend /secret')
        with patch.object(routes.manual_ingest, 'ingest', side_effect=accepted_then_error):
            status, error = self.request('/api/v1/ingests', body, key='accepted')
        self.assertEqual((status, error['code']), (502, 'SERVICE_UNAVAILABLE'))
        self.assertEqual(error['details']['context'], asdict(self.context))
        self.assertEqual(error['details']['operation']['state'], 'FINISHED')
        self.assertNotIn('secret', json.dumps(error))

    def test_admission_errors_keep_contract_before_and_after_acceptance(self):
        original = routes.manual_ingest.ingest
        for accepted in (False, True):
            for index, error_type in enumerate((routes.admission.AdmissionDenied,
                                                routes.admission.AdmissionConflict, routes.admission.GateTimeout)):
                with self.subTest(accepted=accepted, error_type=error_type):
                    body = self.upload()
                    body['destination']['physical_name'] = 'admission_' + str(index)
                    def fail(*args, **kwargs):
                        if accepted:
                            original(*args, **kwargs)
                        raise error_type('private admission implementation details')
                    with patch.object(routes.manual_ingest, 'ingest', side_effect=fail):
                        status, error = self.request('/api/v1/ingests', body,
                            key='admission-' + str(accepted) + '-' + str(index))
                    self.assertEqual((status, error['code']), (error_type.status, error_type.code))
                    if accepted:
                        self.assertEqual(error['details']['context'], asdict(self.context))
                        self.assertEqual(error['details']['operation']['state'], 'FINISHED')
                    else:
                        self.assertNotIn('operation', error.get('details', {}))
                    self.assertNotIn('private admission', json.dumps(error))

    def test_real_draining_export_rejected_with_maintenance_without_operation_or_sql(self):
        ref = self.boundary.existing()
        with routes.control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            routes.admission._ensure(db, self.context.warehouse_id)
            db.execute("UPDATE warehouse_controls SET state='DRAINING' WHERE warehouse_id=?",
                       (self.context.warehouse_id,))
        with patch.object(routes.manual_export, '_profile_ref', return_value='test-profile'):
            status, error = self.request('/api/v1/table-exports',
                dict(context=asdict(self.context), source=asdict(ref), format='csv'), key='blocked')
        self.assertEqual((status, error['code']), (503, 'WAREHOUSE_MAINTENANCE'))
        self.assertFalse(self.boundary.calls)
        with routes.control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_operations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_queries').fetchone()[0], 0)

    def test_inventory_capabilities_are_narrow(self):
        status, reply = self.request('/api/v1/databases/' + self.context.database_id + '/schemas')
        self.assertEqual(status, 200)
        schemas = {s['sql_name']: s for s in reply['items']}
        self.assertTrue(schemas['raw']['capabilities']['wap_models'])
        self.assertNotIn('wap_metadata', schemas['raw']['capabilities'])
        self.assertTrue(schemas['raw']['capabilities']['manual_ingest'])
        self.assertFalse(schemas['marts']['capabilities']['manual_ingest'])
        self.assertTrue(schemas['raw']['capabilities']['wap_build'])
        self.assertFalse(schemas['raw']['capabilities']['wap_publish'])
        import database_inventory as inventory
        with routes.control_store.connect() as db:
            schema = registry.get_schema(db, self.context.schema_id)
            binding = registry.get_binding(db, self.context.database_id)
        for kind, identity in [('TABLE', 'table-uuid'), ('VIEW', 'view-uuid')]:
            item = inventory._item(schema, 'data_engineer', kind.lower(), kind, {identity: kind}, binding=binding)
            self.assertEqual(item['capabilities']['export'], kind == 'TABLE')


if __name__ == '__main__':
    unittest.main()
