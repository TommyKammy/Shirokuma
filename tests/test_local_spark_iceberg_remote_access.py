import base64
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import uuid

from tools.local_experiment.spark_iceberg import remote_access as A

TOKEN = '1' * 32
CLIENT = 'temporary-client-sentinel'
SECRET = 'temporary-secret-sentinel'
BEARER = 'temporary-bearer-sentinel'
ACCESS = 'static-access-sentinel'
S3SECRET = 'static-secret-sentinel'
TABLE = {'table_uuid': '00000000-0000-4000-8000-000000000001',
         'table_location': 's3://shirokuma-lakehouse/l1/local_experiment/spark_' + TOKEN}


class FakeAdmin:
    realm = 'local'

    def __init__(self):
        self.p = A.paths(TOKEN)
        self.entities, self.grants, self.bindings, self.calls = {}, [], {}, []
        self.failure = None
        self.after = None
        self.issue_count = 0
        self.token_response = {'access_token': BEARER, 'token_type': 'Bearer', 'expires_in': 3600}

    def request(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if self.failure:
            self.failure(method, path, body)
        if method == 'GET':
            if path in self.entities:
                return deepcopy(self.entities[path])
            if path == self.p['grants']:
                return {'grants': deepcopy(self.grants)}
            if path == self.p['principal_role'] + '/principals':
                return {'principals': [deepcopy(self.entities[self.p['principal']])]
                        if self.bindings.get(self.p['principal_binding']) else []}
            if path == self.p['catalog_role'] + '/principal-roles':
                return {'roles': [deepcopy(self.entities[self.p['principal_role']])]
                        if self.bindings.get(self.p['catalog_binding']) else []}
            if path in (self.p['principal_binding'], self.p['catalog_binding']):
                key = 'principal_role' if path == self.p['principal_binding'] else 'catalog_role'
                return {'roles': [deepcopy(self.entities[self.p[key]])] if self.bindings.get(path) else []}
            raise A.AccessError('HTTP', outcome='rejected', http_status=404)
        if method == 'POST':
            key = next(k for k in body if k != 'credentialRotationRequired')
            kind = {'principal': 'principal', 'principalRole': 'principal_role', 'catalogRole': 'catalog_role'}[key]
            entity = {**deepcopy(body[key]), 'createTimestamp': 100, 'lastUpdateTimestamp': 100,
                      'entityVersion': 1}
            if kind == 'principal':
                entity['clientId'] = CLIENT
            self.entities[self.p[kind]] = entity
            if self.after:
                self.after(method, path, body)
            return {'principal': deepcopy(entity), 'credentials': {'clientId': CLIENT, 'clientSecret': SECRET}} if kind == 'principal' else deepcopy(entity)
        if method == 'PUT':
            if path == self.p['grants']:
                self.grants.append(deepcopy(body['grant']))
            else:
                self.bindings[path] = True
            return {}
        if method == 'DELETE':
            if path in self.entities:
                del self.entities[path]
            else:
                self.bindings[path.rsplit('/', 1)[0]] = False
            return {}
        raise AssertionError(method)

    def issue_token(self, *credentials):
        if credentials != (CLIENT, SECRET):
            raise AssertionError('wrong principal')
        self.issue_count += 1
        return deepcopy(self.token_response)


class FakeKube:
    def __init__(self):
        self.values, self.creates, self.deletes = {}, [], []
        for i, item in enumerate(A.SERVICES):
            ns, name, _, _, port, target = item
            self.values[('Service', ns, name)] = {'apiVersion': 'v1', 'kind': 'Service',
                'metadata': {'namespace': ns, 'name': name, 'uid': str(uuid.UUID(int=10+i)), 'resourceVersion': '1'},
                'spec': {'type': 'ClusterIP', 'selector': A._selector(item), 'clusterIP': '10.43.0.' + str(10+i),
                         'ports': [{'port': port, 'targetPort': target, 'protocol': 'TCP'}]}}

    def get(self, kind, ns, name):
        return deepcopy(self.values.get((kind, ns, name)))

    def create(self, obj):
        self.creates.append(deepcopy(obj))
        value = deepcopy(obj)
        value['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='1')
        m = value['metadata']
        self.values[('NetworkPolicy', m['namespace'], m['name'])] = value
        return deepcopy(value)

    def delete(self, value):
        self.deletes.append(deepcopy(value))
        m = value['metadata']
        del self.values[('NetworkPolicy', m['namespace'], m['name'])]


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name).resolve()
        os.chmod(self.output, 0o700)
        self.addCleanup(self.tmp.cleanup)
        self.admin, self.kube = FakeAdmin(), FakeKube()
        self.guards = 0
        def guard():
            self.guards += 1
        self.access = A.RemoteAccess(TOKEN, self.output, admin=self.admin, kube=self.kube,
            table_guard=guard, secret_reader=lambda: {'access_key': ACCESS, 'secret_key': S3SECRET})

    def prepare(self):
        return self.access.prepare(TABLE, A.discover_services(self.kube))

    def read_only_access(self):
        self.access = A.RemoteAccess(TOKEN, self.output, admin=self.admin, kube=self.kube,
            table_guard=lambda: None, read_only=True,
            secret_reader=lambda: {'access_key': ACCESS, 'secret_key': S3SECRET})
        return self.access

    def test_read_only_exact_grants_token_and_owned_cleanup(self):
        access = self.read_only_access()
        self.prepare()
        expected = [A.grant(TOKEN, privilege) for privilege in A.READ_ONLY_PRIVILEGES]
        self.assertEqual(self.admin.grants, expected)
        issued = [body['grant'] for method, path, body in self.admin.calls
                  if method == 'PUT' and path == self.admin.p['grants']]
        self.assertEqual(issued, expected)
        self.assertEqual(access.receipt()['polaris_access'], 'read-only')
        self.assertIn('static-bucket-s3-credentials', access.receipt()['limits'])
        self.assertEqual(set(access.secret_provider()), {'aws-credentials', 'polaris-token'})
        self.assertEqual(self.admin.issue_count, 1)
        self.assertTrue(access.cleanup()['verified'])
        self.assertFalse(self.admin.entities)
        self.assertEqual(len(self.kube.deletes), 2)

    def test_read_only_rejects_live_write_grant_before_token_then_removes_own_roles(self):
        access = self.read_only_access()
        self.prepare()
        self.admin.grants.append(A.grant(TOKEN, 'TABLE_WRITE_PROPERTIES'))
        with self.assertRaisesRegex(A.AccessError, 'GRANTS_MISMATCH'):
            access.secret_provider()
        self.assertEqual(self.admin.issue_count, 0)
        self.assertTrue(access.cleanup()['verified'])
        self.assertFalse(self.admin.entities)

    def test_read_only_saved_write_or_partial_grants_cannot_issue_token(self):
        access = self.read_only_access()
        self.prepare()
        for grants in (list(A.PRIVILEGES), ['TABLE_LIST'], ['TABLE_READ_PROPERTIES', 'TABLE_LIST']):
            with self.subTest(grants=grants):
                access.journal['grants'] = grants
                before = len(self.admin.calls)
                with self.assertRaisesRegex(A.AccessError, 'GRANTS_SCOPE_INVALID'):
                    access.secret_provider()
                self.assertEqual(len(self.admin.calls), before)
                self.assertEqual(self.admin.issue_count, 0)

    def test_read_only_mode_requires_boolean_before_any_request_or_file(self):
        for value in (None, 0, 1, 'true', [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(A.AccessError, 'READ_ONLY_MODE_INVALID'):
                A.RemoteAccess(TOKEN, self.output, admin=self.admin, kube=self.kube,
                    table_guard=lambda: None, read_only=value)
        self.assertFalse(self.admin.calls)
        self.assertFalse(list(self.output.iterdir()))

    def test_complete_scope_secret_and_cleanup(self):
        safe = self.prepare()
        self.assertEqual(safe['scope'], 'PRINCIPAL_ROLE:spark_' + TOKEN)
        self.assertEqual(len(safe['data_egress']), 2)
        self.assertEqual(self.admin.grants, [A.grant(TOKEN, x) for x in A.PRIVILEGES])
        material = self.access.secret_provider()
        self.assertEqual(material, self.access.secret_provider())
        self.assertEqual(self.admin.issue_count, 1)
        self.assertEqual(set(material), {'aws-credentials', 'polaris-token'})
        self.assertEqual(self.access.receipt()['token']['expires_in'], 3600)
        encoded = base64.b64encode(SECRET.encode()).decode()
        self.assertNotIn(SECRET, self.access.redact(SECRET + ' ' + encoded))
        self.assertNotIn(encoded, self.access.redact(SECRET + ' ' + encoded))
        receipt_text = (self.output / 'access.json').read_text()
        for secret in (SECRET, BEARER, ACCESS, S3SECRET):
            self.assertNotIn(secret, receipt_text)
        self.assertEqual((self.output / 'access.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.access.cleanup()['verified'])
        self.assertFalse(self.admin.entities)
        self.assertEqual(len(self.kube.deletes), 2)
        self.assertGreaterEqual(self.guards, 16)
        self.assertTrue(self.access.cleanup()['verified'])
        self.assertEqual(len(self.kube.deletes), 2)

    def test_unknown_create_is_never_adopted_or_deleted(self):
        def lost(*args):
            raise A.AccessError('LOST_RESPONSE')
        self.admin.after = lost
        with self.assertRaises(A.AccessError):
            self.prepare()
        self.assertTrue(self.admin.entities)
        self.assertEqual(self.access.receipt()['entities'], {})
        self.assertEqual(self.access.receipt()['pending']['outcome'], 'unknown')
        self.assertFalse(self.access.cleanup()['verified'])
        self.assertFalse(any(x[0] == 'DELETE' for x in self.admin.calls))

    def test_409_create_is_not_adopted(self):
        def reject(method, *args):
            if method == 'POST':
                raise A.AccessError('CONFLICT', outcome='rejected', http_status=409)
        self.admin.failure = reject
        with self.assertRaises(A.AccessError):
            self.prepare()
        self.assertIsNone(self.access.receipt()['pending'])
        self.assertFalse(self.access.receipt()['entities'])
        self.assertTrue(self.access.cleanup()['verified'])

    def test_preexisting_name_stops_all_mutations(self):
        self.admin.entities[self.admin.p['principal']] = {'name': 'foreign'}
        with self.assertRaisesRegex(A.AccessError, 'ENTITY_NAME_COLLISION'):
            self.prepare()
        self.assertTrue(all(x[0] == 'GET' for x in self.admin.calls))
        self.assertFalse(self.kube.creates)

    def test_table_binding_rejects_arbitrary_location(self):
        with self.assertRaises(A.AccessError):
            self.access.prepare(dict(TABLE, table_location=TABLE['table_location'] + 'foreign'), A.discover_services(self.kube))
        self.assertFalse(self.admin.calls)

    def test_service_replacement_stops_before_mutation(self):
        services = A.discover_services(self.kube)
        self.kube.values[('Service', 'shirokuma-dev', 'polaris')]['metadata']['uid'] = str(uuid.uuid4())
        with self.assertRaisesRegex(A.AccessError, 'SERVICE_DRIFT'):
            self.access.prepare(TABLE, services)
        self.assertFalse(self.admin.calls)

    def test_source_guard_failure_no_mutation(self):
        def fail():
            raise ValueError('sensitive source detail')
        self.access.table_guard = fail
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.admin.calls)

    def test_journal_before_write_failure_blocks_create(self):
        with patch.object(A.C, 'write', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.admin.calls)

    def test_not_created_cleanup_needs_no_table_guard(self):
        self.access.table_guard = lambda: (_ for _ in ()).throw(AssertionError('table absent'))
        self.assertTrue(self.access.cleanup()['verified'])
        self.assertFalse(self.admin.calls)

    def test_foreign_assignee_blocks_issue_and_cleanup(self):
        self.prepare()
        original = self.admin.request
        def request(method, path, body=None):
            value = original(method, path, body)
            if method == 'GET' and path == self.admin.p['principal_role'] + '/principals':
                value['principals'].append(dict(value['principals'][0], name='foreign'))
            return value
        self.admin.request = request
        with self.assertRaises(A.AccessError):
            self.access.secret_provider()
        self.assertEqual(self.admin.issue_count, 0)
        self.assertFalse(self.access.cleanup()['verified'])
        self.assertFalse(self.kube.deletes)

    def test_cancel_and_failed_receipt_write_preserves_cancel(self):
        cancellation = KeyboardInterrupt()
        def cancel(*args):
            raise cancellation
        self.admin.after = cancel
        original = self.access._save
        def save():
            if self.access.journal['result'] == 'interrupted':
                raise OSError('disk')
            original()
        with patch.object(self.access, '_save', save):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.prepare()
        self.assertIs(caught.exception, cancellation)

    def test_extra_grant_rejected_before_token(self):
        self.prepare()
        self.admin.grants.append({'type': 'catalog', 'privilege': 'CATALOG_MANAGE_ACCESS'})
        with self.assertRaisesRegex(A.AccessError, 'GRANTS_MISMATCH'):
            self.access.secret_provider()
        self.assertEqual(self.admin.issue_count, 0)

    def test_entity_changed_cleanup_deferred(self):
        self.prepare()
        self.admin.entities[self.admin.p['principal']]['entityVersion'] += 1
        self.assertFalse(self.access.cleanup()['verified'])
        self.assertFalse(self.kube.deletes)
        self.assertFalse(any(x[0] == 'DELETE' for x in self.admin.calls))

    def test_replaced_ingress_never_deleted(self):
        self.prepare()
        for key, value in self.kube.values.items():
            if key[0] == 'NetworkPolicy':
                value['metadata']['uid'] = str(uuid.uuid4())
        self.assertFalse(self.access.cleanup()['verified'])
        self.assertFalse(self.kube.deletes)

    def test_token_invalid_ttl_unknown_and_no_retry(self):
        self.prepare()
        self.admin.token_response['expires_in'] = True
        with self.assertRaisesRegex(A.AccessError, 'TOKEN_LIFETIME_INVALID'):
            self.access.secret_provider()
        with self.assertRaises(A.AccessError):
            self.access.secret_provider()
        self.assertEqual(self.admin.issue_count, 1)
        self.assertFalse(self.access.cleanup()['verified'])
        self.assertNotIn(BEARER, (self.output / 'access.json').read_text())

    def test_public_identifier_does_not_corrupt_operational_paths(self):
        self.access.secret_reader = lambda: {'access_key': 'shirokuma', 'secret_key': S3SECRET}
        self.prepare()
        self.access.secret_provider()
        public = json.dumps({'namespace': 'shirokuma-spark-' + TOKEN,
                             'location': TABLE['table_location']})
        self.assertEqual(self.access.redact(public), public)
        for field in ('AWS_ACCESS_KEY_ID=shirokuma', 'aws_access_key_id = shirokuma',
                      '{"accessKeyId":"shirokuma"}', 'clientId: ' + CLIENT):
            self.assertIn('[REDACTED]', self.access.redact(field))
        self.assertNotIn(S3SECRET, self.access.redact(S3SECRET))

    def test_principal_role_federated_default_false_and_true_rejection(self):
        self.prepare()
        path = self.admin.p['principal_role']
        self.admin.entities[path]['federated'] = False
        self.assertTrue(self.access.secret_provider())
        self.admin.entities[path]['federated'] = True
        with self.assertRaisesRegex(A.AccessError, 'FEDERATED_ROLE_FORBIDDEN'):
            self.access.secret_provider()
        self.assertFalse(self.access.cleanup()['verified'])

    def test_cancel_create_keeps_pending_and_original(self):
        cancellation = KeyboardInterrupt()
        def cancel(*args):
            raise cancellation
        self.admin.after = cancel
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.prepare()
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(self.access.receipt()['result'], 'interrupted')
        self.assertEqual(self.access.receipt()['pending']['outcome'], 'unknown')

    def test_ingress_is_and_scoped_to_run_and_namespace(self):
        for obj, service in zip(A.ingress_policies(TOKEN), A.SERVICES):
            rule = obj['spec']['ingress'][0]
            self.assertEqual(obj['spec']['podSelector']['matchLabels'], A._selector(service))
            self.assertEqual(len(rule['from']), 1)
            self.assertEqual(set(rule['from'][0]), {'namespaceSelector', 'podSelector'})
            self.assertEqual(rule['ports'], [{'protocol': 'TCP', 'port': service[4]}])
            self.assertEqual(rule['from'][0]['podSelector']['matchLabels']['shirokuma.io/run-token'], TOKEN)


class Response(BytesIO):
    def __init__(self, path, value, status=200, content_type='application/json'):
        super().__init__(b'' if value is None else json.dumps(value).encode())
        self.status, self.url = status, A.AUTH.BASE_URL + path
        self.headers = {'Content-Type': content_type}

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response):
        self.response, self.requests = response, []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TransportTests(unittest.TestCase):
    def admin(self, response):
        opener = Opener(response)
        client = A.AdminClient(TOKEN, lambda: None, opener=opener)
        client.auth.realm, client.auth.token = 'local', 'admin-secret-sentinel'
        return client, opener

    def test_exact_own_table_get_and_204_delete(self):
        path = A.paths(TOKEN)['table']
        client, opener = self.admin(Response(path, {'metadata': {}}))
        self.assertEqual(client.request('GET', path), {'metadata': {}})
        self.assertEqual(opener.requests[0][1], 20)
        path = A.paths(TOKEN)['principal']
        client, _ = self.admin(Response(path, None, 204))
        self.assertEqual(client.request('DELETE', path), {})

    def test_foreign_path_and_privilege_no_http(self):
        client, opener = self.admin(None)
        for method, path, body in [('DELETE', A.MANAGEMENT + '/principals/root', None),
                ('GET', A.paths(TOKEN)['table'] + '?raw=1', None),
                ('PUT', A.paths(TOKEN)['grants'], {'grant': {'type': 'catalog', 'privilege': 'CATALOG_MANAGE_ACCESS'}})]:
            with self.assertRaises(A.AccessError):
                client.request(method, path, body)
        self.assertFalse(opener.requests)

    def test_temporary_scope_and_basic_do_not_use_admin_bearer(self):
        client, opener = self.admin(Response(A.AUTH.TOKEN_PATH, {'access_token': BEARER}))
        client.issue_token(CLIENT, SECRET)
        request = opener.requests[0][0]
        self.assertEqual(request.get_header('Authorization'), 'Basic ' + base64.b64encode((CLIENT + ':' + SECRET).encode()).decode())
        self.assertIn(('PRINCIPAL_ROLE%3Aspark_' + TOKEN).encode(), request.data)

    def test_http_error_has_no_body_or_secret(self):
        path = A.paths(TOKEN)['principal']
        error = HTTPError(A.AUTH.BASE_URL + path, 409, SECRET, {}, BytesIO(SECRET.encode()))
        client, _ = self.admin(error)
        with self.assertRaises(A.AccessError) as caught:
            client.request('DELETE', path)
        self.assertEqual(caught.exception.http_status, 409)
        self.assertEqual(caught.exception.outcome, 'rejected')
        self.assertNotIn(SECRET, str(caught.exception))

    def test_origin_content_type_and_limit_rejected(self):
        path = A.paths(TOKEN)['table']
        for change in ('origin', 'content', 'limit'):
            response = Response(path, {'x': 'y'})
            if change == 'origin': response.url = 'http://example.com/'
            if change == 'content': response.headers = {'Content-Type': 'text/html'}
            if change == 'limit': response = Response(path, {'x': 'a' * A.MAX_RESPONSE})
            client, _ = self.admin(response)
            with self.assertRaises(A.AccessError): client.request('GET', path)

    def test_admin_auth_reuses_bounded_reader(self):
        client, _ = self.admin(None)
        with patch.object(client.auth, 'authenticate') as auth:
            client.authenticate()
            auth.assert_called_once_with()

    def test_kube_delete_uses_uid_and_resource_version(self):
        calls = []
        def run(args, **kw):
            calls.append((args, kw)); return '{}'
        kube = A.AccessKube(TOKEN, run=run)
        obj = A.ingress_policies(TOKEN)[0]
        obj['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='123')
        kube.delete(obj)
        body = json.loads(calls[0][1]['body'])
        self.assertEqual(body['preconditions'], {'uid': obj['metadata']['uid'], 'resourceVersion': '123'})
        self.assertIn('--raw', calls[0][0])
        with self.assertRaises(A.AccessError): kube.get('Secret', 'shirokuma-dev', 'polaris-root-credentials')

    def test_s3_reader_fixed_secret_and_synthetic_subprocess(self):
        payload = {'data': {k: base64.b64encode(v.encode()).decode() for k, v in {
            'AWS_ACCESS_KEY_ID': ACCESS, 'AWS_SECRET_ACCESS_KEY': S3SECRET,
            'S3_REGION': 'us-east-1'}.items()}}
        real_popen = subprocess.Popen
        calls = []
        def popen(args, **kwargs):
            calls.append(args)
            return real_popen([sys.executable, '-c', 'import sys;sys.stdout.write(' + repr(json.dumps(payload)) + ')'], **kwargs)
        with patch.object(A.subprocess, 'Popen', side_effect=popen):
            self.assertEqual(A.read_s3_credentials(), {'access_key': ACCESS, 'secret_key': S3SECRET})
        self.assertIn('--request-timeout=10s', calls[0])
        self.assertEqual(calls[0][-5:], ['get', 'secret', 'seaweedfs-s3-application-credentials', '-o', 'json'])

    def test_s3_reader_oversize_and_malformed_are_safe(self):
        real_popen = subprocess.Popen
        for content in (SECRET, 'x' * 65537):
            def popen(args, **kwargs):
                return real_popen([sys.executable, '-c', 'import sys;sys.stdout.write(' + repr(content) + ')'], **kwargs)
            with patch.object(A.subprocess, 'Popen', side_effect=popen):
                with self.assertRaises(A.AccessError) as caught:
                    A.read_s3_credentials()
            self.assertEqual(str(caught.exception), 'S3_SECRET_READ_FAILED')
            self.assertNotIn(SECRET, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
