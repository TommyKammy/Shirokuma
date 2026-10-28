"""Exercise handlers offline, without binding sockets or calling a backend."""
from email.message import Message
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import workbench_server as server
import request_auth as auth


class LogicalWarehouseStatusHTTPTests(unittest.TestCase):
    def request(self, path='/api/status', method='GET'):
        handler = object.__new__(server.WorkbenchHandler)
        handler.path = path
        handler.headers = Message()
        handler.headers['X-Shirokuma-Role'] = 'admin'
        handler.respond = Mock()
        handler.database_request(method)
        return handler.respond.call_args.args

    def test_logical_status_uses_actual_observation_and_never_mutates(self):
        from types import SimpleNamespace
        import runtime_profile
        principal = auth.AuthenticatedPrincipal('owner','admin',True)
        with patch.object(auth,'authenticate_principal',return_value=principal), \
                patch.object(runtime_profile,'current',return_value=SimpleNamespace(mode='logical_v1')), \
                patch.object(server.warehouse_resident,'transition') as mutate:
            for state in ('active','chilled','starting'):
                observation = dict(state=state,warehouse='LOCAL',replicas=0 if state=='chilled' else 2,ready_replicas=0)
                with patch.object(server.warehouse_resident,'status',return_value=observation) as status:
                    result, = self.request()
                self.assertEqual(result,dict(status='healthy',data_mode='database',creation_mode='logical_v1',warehouse=observation))
                status.assert_called_once_with('LOCAL')
            for path in ('/api/warehouse/wake','/api/warehouse/chill','/api/v1/warehouses/LOCAL/wake','/api/v1/warehouses/LOCAL/chill'):
                self.assertEqual(self.request(path,'POST')[-1],404)
            mutate.assert_not_called()

    def test_logical_unavailable_is_safe_and_parameters_remain_strict(self):
        from types import SimpleNamespace
        import runtime_profile
        principal = auth.AuthenticatedPrincipal('owner','admin',True)
        with patch.object(auth,'authenticate_principal',return_value=principal), \
                patch.object(runtime_profile,'current',return_value=SimpleNamespace(mode='logical_v1')), \
                patch.object(server.warehouse_resident,'status',side_effect=RuntimeError('/private/secret kubeconfig')) as status:
            result, = self.request()
            self.assertEqual(result,dict(status='degraded',data_mode='database',creation_mode='logical_v1',warehouse=dict(state='unavailable')))
            self.assertNotIn('private',json.dumps(result))
            status.reset_mock()
            self.assertEqual(self.request('/api/status?target=foreign')[-1],400)
            status.assert_not_called()

    def test_database_v1_response_is_unchanged(self):
        from types import SimpleNamespace
        import runtime_profile
        principal = auth.AuthenticatedPrincipal('owner','admin',True)
        with patch.object(auth,'authenticate_principal',return_value=principal), \
                patch.object(runtime_profile,'current',return_value=SimpleNamespace(mode='database')), \
                patch.object(server.warehouse_resident,'status') as status:
            result, = self.request()
            self.assertEqual(result,dict(status='healthy',data_mode='database'))
            status.assert_not_called()

    def test_legacy_status_keeps_existing_component_observation(self):
        import runtime_profile
        handler = object.__new__(server.WorkbenchHandler)
        handler.path='/api/status';handler.headers=Message();handler.respond=Mock()
        handler.authenticate=Mock(return_value=True)
        handler.authenticated_user='local'
        objects = [dict(kind='Deployment',metadata=dict(name='trino-local'),spec=dict(replicas=1),status=dict(readyReplicas=1))]
        with patch.object(runtime_profile,'active',return_value=False), patch.object(runtime_profile,'current',return_value=None), \
                patch.object(server.warehouse_resident,'kubectl',return_value=json.dumps(dict(items=objects))) as read, \
                patch.object(server.warehouse_resident,'auto_chill_status',return_value={}), \
                patch.object(server,'components',return_value={}), patch.object(server.warehouse_resident,'status') as logical:
            handler.do_GET()
        result, = handler.respond.call_args.args
        self.assertEqual(result['warehouse']['state'],'active')
        self.assertNotIn('creation_mode',result)
        read.assert_called_once_with('get','deploy,sts','-o','json')
        logical.assert_not_called()


class ManagementHTTPTests(unittest.TestCase):
    def test_grant_management_http_pending_cas_replay_inspect_and_admin(self):
        import control_store
        import data_management as dm
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,SHIROKUMA_STATE_DIR=temp):
            with control_store.connect() as db:
                database = dm.create_database(db,'arbitrary','Arbitrary','','test')
            route = '/api/v1/databases/'+database.id+'/grants'
            body = dict(principal_id='alice',maximum_role='bi_viewer',expected_revision=0)
            headers = [('Idempotency-Key','grant-one')]
            value,status = self.request(route,body=body,headers=headers)
            self.assertEqual(status,202)
            self.assertEqual(value['state'],'AWAITING_APPLY')
            self.assertEqual(self.request(route,body=body,headers=headers),(value,202))
            self.assertEqual(self.request(route,body=dict(body,maximum_role='data_engineer'),headers=headers)[1],409)
            self.assertEqual(self.request(route,body=body,headers=[('Idempotency-Key','other')])[1],409)
            self.assertEqual(self.request('/api/v1/operations/'+value['id'],'GET'),(value,200))
            listed,status = self.request(route,'GET')
            self.assertEqual(status,200)
            self.assertEqual(listed['grants'][0]['effective_role'],'revoked')
            with control_store.connect() as db:
                db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','APPLYING',NULL,1,NULL)")
            value,status = self.request(route,body=dict(body,maximum_role='revoked',expected_revision=1),
                headers=[('Idempotency-Key','revoke')])
            self.assertEqual(status,202)
            self.assertEqual(self.request(route,'GET',body={})[1],400)
            self.assertEqual(self.request(route,body=dict(body,actor='forged'),headers=headers)[1],400)
            self.assertEqual(self.request(route,body=body,headers=headers+[('Idempotency-Key','duplicate')])[1],400)
            self.assertEqual(self.request(route,body=body,headers=headers+[('Origin','https://evil.invalid')])[1],403)
            self.principal = auth.AuthenticatedPrincipal('other','data_engineer',False)
            self.assertEqual(self.request(route,'GET',role='data_engineer')[1],403)
            self.assertEqual(self.request('/api/v1/operations/'+value['id'],'GET',role='data_engineer')[1],403)

    def test_grant_roles_malformed_are_400_and_operation_inspection_requires_current_admin(self):
        import control_store
        import data_management as dm
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,SHIROKUMA_STATE_DIR=temp):
            with control_store.connect() as db:
                database = dm.create_database(db,'research','Research','','test')
            route = '/api/v1/databases/'+database.id+'/grants'
            body = dict(principal_id='alice',maximum_role='bi_viewer',expected_revision=0)
            headers = [('Idempotency-Key','grant-one')]
            for malformed in ([],{},['bi_viewer'],{'role':'bi_viewer'},None,True,1,1.5):
                result,status = self.request(route,body=dict(body,maximum_role=malformed),headers=headers)
                self.assertEqual((status,result['code']),(400,'INVALID_GRANT'))
            created,status = self.request(route,body=body,headers=headers)
            self.assertEqual(status,202)
            operation = '/api/v1/operations/'+created['id']
            for principal,role in (
                (auth.AuthenticatedPrincipal('admin','admin',False),'admin'),
                (auth.AuthenticatedPrincipal('admin','admin',True),'bi_viewer'),
                (auth.AuthenticatedPrincipal('admin','data_engineer',False),'data_engineer'),
                (auth.AuthenticatedPrincipal('other','data_engineer',False),'data_engineer')):
                self.principal = principal
                self.assertEqual(self.request(operation,'GET',role=role)[1],403)
            self.principal = auth.AuthenticatedPrincipal('other-admin','admin',True)
            self.assertEqual(self.request(operation,'GET'),(created,200))

    def request(self, path, method='POST', body=None, role='admin', headers=()):
        handler = object.__new__(server.WorkbenchHandler)
        handler.path = path
        handler.headers = Message()
        handler.headers['Host'] = 'localhost:8888'
        handler.headers['X-Shirokuma-Role'] = role
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            handler.headers['Content-Type'] = 'application/json'
            handler.headers['Content-Length'] = str(len(data))
            handler.rfile = io.BytesIO(data)
        for key, value in headers:
            handler.headers[key] = value
        handler.respond = Mock()
        self.assertTrue(handler.management_request(method))
        return handler.respond.call_args.args

    def setUp(self):
        self.principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        self.authentication = patch.object(auth, 'authenticate_principal', side_effect=lambda _: self.principal)
        self.authentication.start()
        self.addCleanup(self.authentication.stop)

    def test_plan_and_handoff_have_no_execution(self):
        with patch('catalog_admin.public_plan', return_value={'id': 'op'}) as plan, \
                patch('catalog_admin.inspect_operation', return_value={'id': 'op', 'state': 'PLANNED'}), \
                patch('catalog_admin.request_action', return_value={'id': 'request', 'requires_operator': True, 'state': 'REQUESTED'}) as handoff, \
                patch('warehouse_operator.run') as execute:
            value, status = self.request('/api/v1/warehouses/LOCAL/plans', body={'generation_id': 'g'},
                headers=[('Idempotency-Key', 'one')])
            self.assertEqual(status, 201)
            plan.assert_called_once_with(self.principal, 'admin', 'g', 'one', phase='full', predecessor_id=None)
            for action in ('apply', 'reconcile'):
                body = {'expected_revision': 1}
                if action == 'apply':
                    body['delivery_digest'] = 'digest'
                value, status = self.request('/api/v1/operations/op/'+action, body=body,
                    headers=[('Idempotency-Key', 'action-key')])
                self.assertEqual(status, 202)
                self.assertTrue(value['requires_operator'])
                self.assertEqual(value['state'], 'REQUESTED')
                handoff.assert_called_with(self.principal, 'admin', 'op', '/api/v1/operations/op/'+action, 'action-key', body)
            execute.assert_not_called()

    def test_strict_payload_headers_origin_and_query(self):
        cases = [('/api/v1/warehouses/LOCAL/plans', {'generation_id': 'g', 'path': '/private'}, []),
                 ('/api/v1/warehouses/LOCAL/plans', {'generation_id': 'g'}, []),
                 ('/api/v1/operations/op/apply', {'expected_revision': 1}, []),
                 ('/api/v1/operations/op/reconcile', {'evidence': {'outcome': 'SUCCEEDED'}}, []),
                 ('/api/v1/operations/op/apply?x=1', {}, []),
                 ('/api/v1/operations/op/apply', {}, [('Origin', 'https://evil.invalid')]),
                 ('/api/v1/operations/op/apply', b'{"x":1,"x":2}', []),
                 ('/api/v1/operations/op/apply', {}, [('X-Shirokuma-Role', 'admin')]),
                 ('/api/v1/operations/op/apply', {}, [('Transfer-Encoding', 'chunked')])]
        with patch('catalog_admin.public_plan') as plan, patch('catalog_admin.inspect_operation') as inspect:
            for path, body, headers in cases:
                with self.subTest(path=path, body=body):
                    result, status = self.request(path, body=body, headers=headers)
                    self.assertGreaterEqual(status, 400)
                    self.assertFalse(result['retryable'])
            plan.assert_not_called()
            inspect.assert_not_called()

    def test_admin_both_required_and_owner_inspect_delegated(self):
        with patch('catalog_admin.inspect_operation', return_value={'id': 'op'}) as inspect:
            for principal, role in [(auth.AuthenticatedPrincipal('owner', 'admin', False), 'admin'),
                                    (auth.AuthenticatedPrincipal('admin', 'admin', True), 'bi_viewer')]:
                self.principal = principal
                self.assertEqual(self.request('/api/v1/operations/op/apply', body={}, role=role)[1], 403)
                self.assertEqual(self.request('/api/v1/operations/op', 'GET', role=role)[1], 200)
            self.assertEqual(inspect.call_count, 2)

    def test_safe_nonretryable_errors_and_get_body(self):
        error = RuntimeError('credential secret')
        error.code, error.status = 'PRIVATE_CODE', 404
        with patch('catalog_admin.inspect_operation', side_effect=error):
            value, status = self.request('/api/v1/operations/op', 'GET')
            self.assertEqual(status, 409)
            self.assertEqual(value['code'], 'OPERATION_UNAVAILABLE')
            self.assertFalse(value['retryable'])
            self.assertNotIn('credential', json.dumps(value))
        self.assertEqual(self.request('/api/v1/operations/op', 'GET', body={})[1], 400)

    def test_operation_error_preserves_only_safe_code_and_status(self):
        from warehouse_operations import OperationError
        for code, expected_status in (('OPERATION_NOT_FOUND', 404), ('REVISION_CONFLICT', 409),
                                      ('IDEMPOTENCY_CONFLICT', 409), ('DELIVERY_CONFLICT', 409)):
            with self.subTest(code=code):
                error = OperationError(code, expected_status)
                error.args = ('private diagnostic credential',)
                error.details = {'secret': 'private diagnostic credential'}
                with patch('catalog_admin.inspect_operation', side_effect=error):
                    result, status = self.request('/api/v1/operations/op', 'GET')
                self.assertEqual(status, expected_status)
                self.assertEqual(result['code'], code)
                self.assertEqual(result['message'], 'Management request rejected')
                self.assertFalse(result['retryable'])
                self.assertEqual(result['details'], {})
                self.assertNotIn('private diagnostic', json.dumps(result))

    def test_other_routes_untouched(self):
        handler = object.__new__(server.WorkbenchHandler)
        for path in ('/api/v1/queries', '/api/status', '/api/v2/query'):
            handler.path = path
            self.assertFalse(handler.management_request('GET'))

    def test_create_inspect_replay_and_conflict_are_authenticated_without_optional_dependencies(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory), \
                patch.dict(sys.modules, yaml=None, boto3=None):
            body = dict(sql_catalog='marketing_forecast_2026', profile_id='local-polaris-1.8.0')
            headers = [('Idempotency-Key', 'create-one')]
            created, status = self.request('/api/v1/databases', body=body, headers=headers)
            self.assertEqual(status, 202)
            replayed, status = self.request('/api/v1/databases', body=body, headers=headers)
            self.assertEqual(status, 202)
            self.assertEqual(replayed, created)
            inspected, status = self.request('/api/v1/operations/'+created['operation_id'], 'GET')
            self.assertEqual(status, 200)
            self.assertEqual(inspected['database_id'], created['database_id'])
            changed, status = self.request('/api/v1/databases', body=dict(body, sql_catalog='changed'), headers=headers)
            self.assertEqual((status, changed['code']), (409, 'IDEMPOTENCY_CONFLICT'))
            for payload in (dict(body, endpoint='private'), dict(body, secret='private')):
                self.assertEqual(self.request('/api/v1/databases', body=payload, headers=headers)[1], 400)
            self.principal = auth.AuthenticatedPrincipal('reader', 'bi_viewer', False)
            self.assertEqual(self.request('/api/v1/databases', body=body, role='bi_viewer', headers=headers)[1], 403)
            self.assertEqual(self.request('/api/v1/operations/'+created['operation_id'], 'GET', role='bi_viewer')[1], 403)

    def test_runtime_attestation_is_admin_only_and_has_no_private_inputs(self):
        public = dict(version=1, generation_id='g', profile_ref='public-reference', databases={})
        with patch('runtime_profile.generation_attestation', return_value=public) as attest:
            self.assertEqual(self.request('/api/v1/runtime', 'GET'), (public, 200))
            self.assertEqual(self.request('/api/v1/runtime', 'GET', body={})[1], 400)
            self.assertEqual(self.request('/api/v1/runtime', 'GET', role='bi_viewer')[1], 403)
            self.assertEqual(attest.call_count, 1)

    def test_normal_server_and_old_route_without_yaml_or_operator_import(self):
        source = """
import sys, io
from unittest.mock import Mock, patch
sys.path.insert(0, 'tools/local_experiment')
sys.modules['yaml'] = None
import workbench_server as server
assert 'warehouse_operator' not in sys.modules
assert 'database_generation' not in sys.modules
handler = object.__new__(server.WorkbenchHandler)
handler.path = '/api/session'
handler.authorize = lambda *args: 'bi_viewer'
handler.respond = Mock()
handler.authenticated_user = 'legacy'
handler.maximum_role = 'bi_viewer'
with patch('runtime_profile.active', return_value=False):
    handler.do_GET()
assert handler.respond.call_args.args[0]['username'] == 'legacy'
assert 'warehouse_operator' not in sys.modules
assert 'database_generation' not in sys.modules
"""
        result = subprocess.run(['rtk', 'proxy', 'python3', '-c', source], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_durable_handoff_idempotency_and_inspect_actual_core(self):
        import control_store
        import data_management as dm
        import database_generation
        import warehouse_operations as core
        from test_database_generation import spec
        bundle = database_generation.render(spec(1))
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, SHIROKUMA_STATE_DIR=temp):
            with control_store.connect() as db:
                for key, value in bundle['manifest']['spec']['databases'].items():
                    db.execute('INSERT INTO databases VALUES (?,?,?,?,?,?,?)',
                               (key, value['sql_catalog'], key, '', 'user', 'READY', 1))
                    dm.create_binding(db, key, **{k: v for k, v in value['binding'].items() if k != 'desired_revision'})
                    for schema in value['schemas']:
                        db.execute('INSERT INTO schemas VALUES (?,?,?,?,?,?,?)',
                            (schema['id'], key, schema['sql_name'], schema['sql_name'], schema['purpose'], 'user', schema['revision']))
            refs = bundle['tofu']['bootstrap_secret_refs']+bundle['tofu']['deferred_secret_refs']
            observations = [dict(ref, exists=False, uid=None, resource_version=None, immutable=None) for ref in refs]
            plan = core.create_plan('admin', '/plans', 'key', bundle, observations)
            body = {'expected_revision': plan['revision'], 'delivery_digest': plan['delivery_digest']}
            route = '/api/v1/operations/'+plan['id']+'/apply'
            headers = [('Idempotency-Key', 'durable')]
            first, status = self.request(route, body=body, headers=headers)
            self.assertEqual(status, 202)
            self.assertEqual(first['state'], 'REQUESTED')
            self.assertEqual(self.request(route, body=body, headers=headers)[0]['id'], first['id'])
            conflict, status = self.request(route, body=dict(body, expected_revision=99), headers=headers)
            self.assertEqual((status, conflict['code']), (409, 'IDEMPOTENCY_CONFLICT'))
            conflict, status = self.request(route, body=dict(body, expected_revision=99),
                headers=[('Idempotency-Key', 'stale-revision')])
            self.assertEqual((status, conflict['code']), (409, 'REVISION_CONFLICT'))
            conflict, status = self.request(route, body=dict(body, delivery_digest='wrong'),
                headers=[('Idempotency-Key', 'wrong-delivery')])
            self.assertEqual((status, conflict['code']), (409, 'DELIVERY_CONFLICT'))
            view, status = self.request('/api/v1/operations/'+plan['id'], 'GET')
            self.assertEqual(status, 200)
            self.assertEqual([r['id'] for r in view['handoffs']], [first['id']])
            self.principal = auth.AuthenticatedPrincipal('nonowner', 'data_engineer', False)
            result, status = self.request('/api/v1/operations/'+plan['id'], 'GET', role='data_engineer')
            self.assertEqual((status, result['code']), (403, 'FORBIDDEN'))
            self.assertFalse(result['retryable'])
            self.principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
            result, status = self.request('/api/v1/operations/missing', 'GET')
            self.assertEqual((status, result['code']), (404, 'OPERATION_NOT_FOUND'))
            self.assertFalse(result['retryable'])
