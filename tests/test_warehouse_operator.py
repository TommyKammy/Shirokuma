import argparse
import base64
import copy
import contextlib
import json
import io
import os
import posixpath
from pathlib import Path
import subprocess
import sys
import tempfile
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/local_experiment'))
sys.path.insert(0, str(ROOT/'tests'))
import database_generation as generation
import local_gitops
import request_auth
import warehouse_operator as worker
from test_database_generation import spec


class OperatorTests(unittest.TestCase):
    def test_managed_receiver_borrows_http_get_without_receipt(self):
        import database_creation_bridge
        calls = []
        expected = {'transport': 'managed', 'observed': True}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                calls.append((self.command, self.path))
                payload = json.dumps(expected).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            channel, peer = socket.socketpair()
            with channel, peer:
                managed = database_creation_bridge.Managed(channel, {}, None)
                self.assertFalse(hasattr(managed, 'receipt'))
                request = worker.urllib.request.Request(
                    'http://127.0.0.1:'+str(server.server_port)+'/managed-transport')
                self.assertEqual(worker.Operator.http(managed, request), expected)
                self.assertEqual(calls, [('GET', '/managed-transport')])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    @contextlib.contextmanager
    def readonly_fixture(self):
        """Real core claims/auth and HTTP sends; only lab resources are synthetic."""
        import control_store as store
        import data_management as dm
        import warehouse_operations as core
        import runtime_profile
        from database_provision import permissions
        self.intent_patch.stop()
        counts = dict(oauth=0, sql=0, lose_oauth=True, lose_sql=False)
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                # Every actual HTTP boundary admits an independent writer.
                with store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                path = unquote(urlsplit(self.path).path)
                if path.endswith('/oauth/tokens'):
                    counts['oauth'] += 1
                    if counts.pop('lose_oauth', False):
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    result = {'access_token': 'separate-short-token'}
                elif path == '/v1/statement':
                    counts['sql'] += 1
                    if counts.get('pause_sql'):
                        counts['pause_sql'][0].set()
                        counts['pause_sql'][1].wait(10)
                    if counts.get('revoke_after_sql'):
                        fixture.private('runtime-accounts', worker.canonical(dict(admin=dict(role='bi_viewer',
                            platform_admin=False, salt='12'*16, password_hash=request_auth.password_hash('test', '12'*16)))))
                    if counts.get('lose_sql'):
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    database = next(e for e in fixture.bundle['tofu']['databases'].values()
                                    if '"'+e['sql_catalog']+'".information_schema' in body.decode())
                    result = dict(id='http-query-'+str(counts['sql']),
                        columns=[{'name': n} for n in ('verified_user', 'catalog_name', 'schema_name')],
                        data=[[self.headers['X-Trino-User'], database['sql_catalog'], name] for name in generation.SCHEMAS])
                    if counts.get('pages_per_statement', 1) > 1:
                        counts.setdefault('pending_pages', {})[result['id']] = result
                        result = dict(id=result['id'], nextUri='https://'+self.headers['Host']+
                                      '/v1/statement/'+result['id']+'/1')
                elif path.startswith('/v1/statement/'):
                    counts['page_gets'] = counts.get('page_gets', 0)+1
                    backend_id, page = path.rsplit('/', 2)[-2:]
                    if int(page) == counts['pages_per_statement']-1:
                        result = counts['pending_pages'][backend_id]
                        if counts.get('revoke_after_sql_page'):
                            fixture.private('runtime-accounts', worker.canonical(dict(admin=dict(role='bi_viewer',
                                platform_admin=False, salt='12'*16,
                                password_hash=request_auth.password_hash('test', '12'*16)))))
                    else:
                        result = dict(id=backend_id, nextUri='https://'+self.headers['Host']+
                                      '/v1/statement/'+backend_id+'/'+str(int(page)+1))
                elif path == '/api/v1/runtime':
                    result = runtime_profile.validate_generation(fixture.config['runtime_profile'], fixture.bundle)
                    if counts.get('runtime_drift'):
                        result = dict(result, generation_id='other')
                else:
                    try:
                        result = fixture.apis[self.headers['Polaris-Realm']]('GET', path)
                    except permissions.APIError as error:
                        self.send_error(error.status)
                        return
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        cert, key = self.root/'test-cert.pem', self.root/'test-key.pem'
        subprocess.run(['rtk', 'proxy', 'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, capture_output=True)
        cert.chmod(0o600); key.chmod(0o600)
        servers = [ThreadingHTTPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        servers[1].socket = context.wrap_socket(servers[1].socket, server_side=True)
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            http_origin = 'http://127.0.0.1:'+str(servers[0].server_port)
            self.candidate['trino'] = dict(endpoint_url='https://127.0.0.1:'+str(servers[1].server_port), ca_file=str(cert))
            for entry in self.candidate['databases'].values():
                entry['polaris']['endpoint_url'] = http_origin
            self.private('candidate', worker.canonical(self.candidate))
            self.runtime_profile.update(sql_endpoint_url=http_origin, trino_ca_file=str(cert))
            self.private('runtime.json', worker.canonical(self.runtime_profile))
            # Distinct runtime origins, served by the same temporary HTTP fixture.
            self.config['runtime_endpoint'] = http_origin.replace('127.0.0.1', 'localhost')
            self.private('config', worker.canonical(self.config))
            salt = '12'*16
            self.private('runtime-accounts', worker.canonical(dict(admin=dict(role='admin', platform_admin=True,
                salt=salt, password_hash=request_auth.password_hash('test', salt)))))
            self.private('auth', worker.canonical({'Authorization': 'Basic '+base64.b64encode(b'admin:test').decode(),
                                                'X-Shirokuma-Role': 'admin'}))
            with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(self.root/'runtime-accounts')):
                operator = worker.Operator(self.path)
                operator.bind(self.operation)
                operator.check_receipt(self.operation)
                objects, kubectl, ignored, requests = self.candidate_backend(operator, actual_runtime=True)
                children_before = copy.deepcopy(operator.receipt['children_before'])
                passwords = [self.runtime_backend['platform_admin']]
                for database in self.runtime_backend['databases'].values():
                    passwords.extend(database['roles'].values())
                hashes = []
                for credential in passwords:
                    hashed = subprocess.run(['rtk', 'proxy', 'htpasswd', '-niB', '-C', '4', credential['username']],
                        input=credential['password']+'\n', text=True, capture_output=True, check=True)
                    hashes.append(hashed.stdout.strip())
                ref = operator.bundle['tofu']['shared']['trino_auth_secret']
                objects[('secret', ref['namespace'], ref['name'])]['data']['password.db'] = base64.b64encode(
                    ('\n'.join(hashes)+'\n').encode()).decode()
                del operator.root_api  # Actual root OAuth and GET requests go through HTTP.
                with store.connect() as db:
                    for name, value in self.bundle['manifest']['spec']['databases'].items():
                        db.execute('INSERT INTO databases VALUES (?,?,?,?,?,?,?)',
                            (name, value['sql_catalog'], name, '', 'user', 'READY', 1))
                        dm.create_binding(db, name, **{k:v for k,v in value['binding'].items() if k != 'desired_revision'})
                        for schema in value['schemas']:
                            db.execute('INSERT INTO schemas VALUES (?,?,?,?,?,?,?)',
                                (schema['id'], name, schema['sql_name'], schema['sql_name'], schema['purpose'], 'user', schema['revision']))
                plan = core.create_plan('admin', '/warehouse/configuration', 'readonly-http', self.bundle,
                                        self.operation['secret_observations'])
                self.operation = plan
                operator.bind(plan)
                operator.check_receipt(plan)
                # Produce the five private ACKs through the real execute_step and
                # core callbacks. No test writes ACKs or core SUCCEEDED states.
                with patch.object(operator, 'kubectl', side_effect=kubectl), \
                        patch.object(operator, 'upload'), patch.object(operator, 'validate_saved_plan'), \
                        patch.object(operator, 'suspend_children'), patch.object(operator, 'switch_source'), \
                        patch.object(operator, 'apply_children'), patch.object(worker, '_apply_saved_plan'):
                    # Carry the synthetic retained resource identities into the new
                    # operation's receipt, before running its real stage callbacks.
                    operator.receipt['source_uid'] = 'uid-'+local_gitops.SOURCE
                    operator.receipt['children_before'] = children_before
                    operator.save()
                    unknown = core.apply(plan['id'], actor='admin', expected_revision=plan['revision'],
                        delivery_digest=plan['delivery_digest'], authorize=operator.authorize,
                        observe_secrets=operator.observe_secrets, execute_step=operator.execute_step, verify=operator.verify)
                    self.assertEqual(unknown['state'], 'UNKNOWN')
                    self.assertEqual(counts['sql'], 0)
                    parent = operator.receipt_for(plan['id'])
                    raw = worker.private_file(parent)
                    yield core, operator, unknown, parent, raw, counts, objects
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()

    def recover_readonly(self, core, operator, plan, raw):
        return core.verify_readonly(plan['id'], actor='admin', expected_revision=plan['revision'],
            delivery_digest=plan['delivery_digest'], receipt_digest=worker.digest(raw), operator=operator)

    def test_readonly_http_completion_observation_then_same_id_finalize(self):
        with self.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
            before = json.loads(raw)
            observed = core.reconcile_from_observer(plan['id'], actor='admin', observer=lambda p: operator.reconcile(p, 'verify'))
            self.assertEqual(observed['state'], 'UNKNOWN')
            self.assertEqual((counts['oauth'], counts['sql']), (1, 0))
            completed = self.recover_readonly(core, operator, observed, raw)
            self.assertEqual(completed['state'], 'UNKNOWN')
            self.assertEqual(counts['sql'], 2)
            pointer = json.loads(parent.read_bytes()).pop('readonly_recovery')
            self.assertEqual(json.loads(parent.read_bytes()), dict(before, readonly_recovery=pointer))
            self.assertEqual(worker.private_file(operator.receipt_path.parent/'parent-before.json'), raw)
            self.assertTrue(operator.receipt['verification_complete'])
            with self.assertRaises(core.OperationError):
                self.recover_readonly(core, operator, completed, parent.read_bytes())
            with patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref':'snapshot:exact'}):
                awaiting = core.reconcile_from_observer(plan['id'], actor='admin', observer=lambda p: operator.reconcile(p, 'verify'))
                self.assertEqual(awaiting['state'], 'AWAITING_APPLY')
                with patch.object(operator, 'execute_step', side_effect=AssertionError('no replay')):
                    final = core.apply(plan['id'], actor='admin', expected_revision=awaiting['revision'],
                        delivery_digest=awaiting['delivery_digest'], authorize=operator.authorize,
                        observe_secrets=operator.observe_secrets, execute_step=operator.execute_step, verify=operator.verify)
            self.assertEqual(final['state'], 'APPLIED')
            self.assertEqual(counts['sql'], 2)
            with core.control_store.connect() as db:
                self.assertEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0], 'OPEN')
                self.assertEqual(db.execute('SELECT applied_generation_id FROM catalog_bindings').fetchone()[0], plan['generation_id'])
            self.assertEqual(json.loads(parent.read_bytes()), dict(before, readonly_recovery=pointer))

    def test_readonly_http_accepted_sql_response_loss_is_permanent_hold(self):
        with self.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
            counts['lose_sql'] = True
            entered, release = threading.Event(), threading.Event()
            counts['pause_sql'] = (entered, release)
            errors = []
            def recover():
                try:
                    self.recover_readonly(core, operator, plan, raw)
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=recover)
            thread.start()
            try:
                self.assertTrue(entered.wait(20))
                with self.assertRaises(core.OperationError) as busy:
                    self.recover_readonly(core, operator, plan, raw)
                self.assertEqual(busy.exception.code, 'WAREHOUSE_OPERATOR_BUSY')
            finally:
                release.set()
                thread.join(20)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RuntimeError)
            self.assertEqual(counts['sql'], 1)
            child = json.loads(worker.private_file(operator.receipt_path))
            self.assertTrue(next(iter(child['sql'].values()))['dispatched'])
            current = core.inspect(plan['id'], actor='admin')
            for _ in range(2):
                with self.assertRaises(core.OperationError):
                    self.recover_readonly(core, operator, current, parent.read_bytes())
                current = core.reconcile_from_observer(plan['id'], actor='admin', observer=lambda p: operator.reconcile(p, 'verify'))
                self.assertEqual(current['state'], 'UNKNOWN')
            self.assertEqual(counts['sql'], 1)
            self.assertEqual(len(list(parent.parent.glob(plan['id']+'-verify-*'))), 1)

    def test_readonly_parent_and_current_material_exclusions_precede_claim(self):
        with self.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
            original = json.loads(raw)
            for change in ('sql', 'ack', 'declaration', 'pointer'):
                modified = copy.deepcopy(original)
                if change == 'sql':
                    modified['sql']['any-entry'] = {}
                elif change == 'ack':
                    modified['acknowledgements'].pop(worker.STEPS[0])
                elif change == 'declaration':
                    modified['verify_declaration'] = {}
                else:
                    modified['readonly_recovery'] = {}
                worker.save_private(parent, modified)
                with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError, TypeError, KeyError)):
                    self.recover_readonly(core, operator, plan, parent.read_bytes())
                parent.write_bytes(raw)
            for field in ('managed_handoff', 'managed_transport'):
                with patch.dict(operator.config, {field:{}}), self.assertRaises(RuntimeError):
                    self.recover_readonly(core, operator, plan, raw)
            for name in ('plan', 'terraform-state', 'runtime-backend', 'runtime.json', 'candidate', 'test-cert.pem'):
                path = self.root/name
                before = path.read_bytes()
                path.write_bytes(before+b' ')
                with self.subTest(drift=name), self.assertRaises((RuntimeError, ValueError)):
                    self.recover_readonly(core, operator, plan, raw)
                path.write_bytes(before)
            ref = self.bundle['tofu']['bootstrap_secret_refs'][0]
            metadata = objects[('secret', ref['namespace'], ref['name'])]['metadata']
            for field in ('uid', 'resourceVersion'):
                before = metadata[field]
                metadata[field] = 'drift'
                with self.subTest(secret=field), self.assertRaises(core.OperationError):
                    self.recover_readonly(core, operator, plan, raw)
                metadata[field] = before
            counts['runtime_drift'] = True
            with self.assertRaises(RuntimeError):
                self.recover_readonly(core, operator, plan, raw)
            counts['runtime_drift'] = False
            with core.control_store.connect() as db:
                db.execute('UPDATE databases SET revision=revision+1')
            with self.assertRaises(core.OperationError):
                self.recover_readonly(core, operator, plan, raw)
            with core.control_store.connect() as db:
                db.execute('UPDATE databases SET revision=revision-1')
                self.assertEqual(db.execute('SELECT count(*) FROM warehouse_action_requests').fetchone()[0], 0)
            for revision, delivery, receipt in ((plan['revision']-1, plan['delivery_digest'], worker.digest(raw)),
                    (plan['revision'], 'a'*64, worker.digest(raw)), (plan['revision'], plan['delivery_digest'], 'a'*64)):
                with self.assertRaises((core.OperationError, RuntimeError)):
                    core.verify_readonly(plan['id'], actor='admin', expected_revision=revision,
                        delivery_digest=delivery, receipt_digest=receipt, operator=operator)
            self.assertEqual((counts['oauth'], counts['sql']), (1, 0))

    def test_readonly_authentication_read_path_before_and_between_sql_leaves(self):
        with self.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
            accounts = self.root/'runtime-accounts'
            before = accounts.read_bytes()
            value = json.loads(before)
            value['admin']['platform_admin'] = False
            accounts.write_bytes(worker.canonical(value))
            with patch.object(request_auth, 'authenticate_principal', wraps=request_auth.authenticate_principal) as auth:
                with self.assertRaises(core.OperationError):
                    self.recover_readonly(core, operator, plan, raw)
                self.assertGreater(auth.call_count, 0)
            accounts.write_bytes(before)
            counts['revoke_after_sql'] = True
            with patch.object(request_auth, 'authenticate_principal', wraps=request_auth.authenticate_principal) as auth:
                with self.assertRaises(PermissionError):
                    self.recover_readonly(core, operator, plan, raw)
                self.assertGreater(auth.call_count, 2)
            self.assertEqual(counts['sql'], 1)
            accounts.write_bytes(before)
            current = core.inspect(plan['id'], actor='admin')
            with self.assertRaises(core.OperationError):
                self.recover_readonly(core, operator, current, parent.read_bytes())
            self.assertEqual(counts['sql'], 1)

    def test_readonly_lost_return_after_genuine_completion_observes_without_sql(self):
        with self.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
            verify = operator.verify
            def lose_return(current):
                verify(current)
                raise RuntimeError('return lost after durable completion')
            with patch.object(operator, 'verify', side_effect=lose_return), self.assertRaises(RuntimeError):
                self.recover_readonly(core, operator, plan, raw)
            self.assertTrue(json.loads(worker.private_file(operator.receipt_path))['verification_complete'])
            sql = counts['sql']
            with patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref':'snapshot:exact'}):
                result = core.reconcile_from_observer(plan['id'], actor='admin', observer=lambda p: operator.reconcile(p, 'verify'))
            self.assertEqual(result['state'], 'AWAITING_APPLY')
            self.assertEqual(counts['sql'], sql)

    def test_readonly_claim_child_pointer_interruptions_never_rearm(self):
        self.intent_patch.stop()
        for boundary in ('claim', 'child', 'pointer'):
            with self.subTest(boundary=boundary):
                fixture = OperatorTests()
                fixture.setUp()
                try:
                    with fixture.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
                        save = worker.save_private
                        def fail_pointer(path, value):
                            if path == parent and 'readonly_recovery' in value:
                                raise RuntimeError('test interruption before pointer')
                            return save(path, value)
                        fault = (patch.object(operator, 'create_readonly_child', side_effect=RuntimeError('claim interruption'))
                            if boundary == 'claim' else patch.object(worker, 'save_private', side_effect=fail_pointer)
                            if boundary == 'child' else patch.object(operator, 'verify', side_effect=RuntimeError('pointer interruption')))
                        with fault, self.assertRaises(RuntimeError):
                            fixture.recover_readonly(core, operator, plan, raw)
                        current = core.inspect(plan['id'], actor='admin')
                        self.assertEqual((current['state'], current['revision']), ('UNKNOWN', plan['revision']+1))
                        with self.assertRaises(core.OperationError) as hold:
                            fixture.recover_readonly(core, operator, current, parent.read_bytes())
                        self.assertEqual(hold.exception.code, 'READONLY_VERIFY_HOLD')
                        directories = list(parent.parent.glob(plan['id']+'-verify-*'))
                        self.assertEqual(len(directories), 0 if boundary == 'claim' else 1)
                        self.assertEqual((counts['oauth'], counts['sql']), (1, 0))
                        if boundary != 'pointer':
                            self.assertEqual(parent.read_bytes(), raw)
                        else:
                            value = json.loads(parent.read_bytes())
                            value.pop('readonly_recovery')
                            self.assertEqual(value, json.loads(raw))
                finally:
                    fixture.doCleanups()

    def test_readonly_python_cli_requires_complete_opt_in_flags(self):
        import catalog_admin
        base = ['reconcile', '--config', 'unused', '--operation-id', 'op', '--step', 'verify']
        digest = 'a'*64
        for flags in (['--verify-readonly'], ['--expected-revision', '9'], ['--receipt-digest', digest],
                ['--delivery-digest', digest], ['--verify-readonly', '--expected-revision', '0',
                '--delivery-digest', digest, '--receipt-digest', digest]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                catalog_admin.main(base+flags)

    def test_readonly_material_observations_are_bounded_across_http_pages_and_revocation(self):
        self.intent_patch.stop()
        phase_counts = []
        for pages, revoke in ((1, False), (4, False), (4, True)):
            with self.subTest(pages=pages, revoke=revoke):
                fixture = OperatorTests()
                fixture.setUp()
                try:
                    with fixture.readonly_fixture() as (core, operator, plan, parent, raw, counts, objects):
                        counts.update(pages_per_statement=pages, revoke_after_sql_page=revoke)
                        with patch.object(operator, 'check_readonly_material', wraps=operator.check_readonly_material) as material, \
                                patch.object(operator, 'observe_secrets', wraps=operator.observe_secrets) as secrets, \
                                patch.object(request_auth, 'authenticate_principal', wraps=request_auth.authenticate_principal) as auth:
                            if revoke:
                                with self.assertRaises(PermissionError):
                                    fixture.recover_readonly(core, operator, plan, raw)
                                self.assertEqual(counts['sql'], 1)
                                self.assertEqual(counts['page_gets'], pages-1)
                                self.assertGreater(auth.call_count, pages)
                                self.assertEqual(material.call_count, 1)  # entry only; no successful completion
                                oauth, sql = counts['oauth'], counts['sql']
                                key, entry = next(iter(operator.bundle['tofu']['databases'].items()))
                                ref = entry['trino_secret']
                                credential = {k:base64.b64decode(v).decode() for k,v in
                                    operator.secret_objects[(ref['namespace'], ref['name'])]['data'].items()}
                                with self.assertRaises(PermissionError):
                                    operator.consumer_token(
                                        fixture.candidate['databases'][key]['polaris']['endpoint_url'], entry['realm'], credential)
                                self.assertEqual((counts['oauth'], counts['sql']), (oauth, sql))
                                continue
                            fixture.recover_readonly(core, operator, plan, raw)
                            verification = (material.call_count, secrets.call_count)
                            self.assertEqual(verification, (2, 5))
                            self.assertEqual(counts.get('page_gets', 0), 2*(pages-1))
                            material.reset_mock(); secrets.reset_mock()
                            oauth, sql = counts['oauth'], counts['sql']
                            with patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref':'snapshot:exact'}):
                                observed = core.reconcile_from_observer(plan['id'], actor='admin',
                                    observer=lambda p: operator.reconcile(p, 'verify'))
                            self.assertEqual(observed['state'], 'AWAITING_APPLY')
                            observation = (material.call_count, secrets.call_count)
                            self.assertEqual(observation, (2, 4))
                            self.assertEqual((counts['oauth'], counts['sql']), (oauth, sql))
                            phase_counts.append((verification, observation))
                finally:
                    fixture.doCleanups()
        self.assertEqual(phase_counts[0], phase_counts[1])

    def test_bootstrap_completed_receipt_observes_real_provisioning_without_business_posts(self):
        import database_provision
        import database_creation_recovery as recovery
        from tests.test_database_provision import API
        operator = worker.Operator.__new__(worker.Operator)
        operator.phase = 'bootstrap'
        operator.predecessor_id = None
        operator.bundle = generation.render(spec(3))
        operator.binding = dict(generation_id='observation-generation')
        operator.receipt = dict(dispatches=[],provisioning={})
        endpoints,credentials,objects,apis = {},{'databases':{}},{},{}
        for index,(key,entry) in enumerate(operator.bundle['tofu']['databases'].items()):
            endpoint = 'http://127.0.0.1:'+str(18200+index)
            endpoints['polaris-'+str(index)] = endpoint
            credentials['databases'][key] = {'polaris':{'endpoint_url':endpoint}}
            api = API(); apis[entry['realm']] = api
            database_provision.provision(api,key,entry,generation.SCHEMAS,operator.receipt['provisioning'],lambda state:None)
            ref = entry['root_secret']
            objects[(ref['namespace'],ref['name'])] = {'data':{k:base64.b64encode(v.encode()).decode()
                for k,v in dict(client_id='root',client_secret='root-test-secret',realm=entry['realm']).items()}}
        operator.secret_objects = objects
        operator.config = {'managed_transport':{'endpoints':endpoints}}
        operator._observation_operation = {'id':'bootstrap'}
        calls = []
        def read_api(endpoint,realm,root):
            def request(method,path,body=None):
                self.assertEqual(method,'GET')
                calls.append((method,path))
                return apis[realm](method,path,body)
            return request
        with patch.object(operator,'check_receipt'),patch.object(operator,'save'),patch.object(operator,'assert_intent'),\
                patch.object(operator,'observe_secrets'),patch.object(operator,'candidate_credentials',return_value=credentials),\
                patch.object(operator,'verification_configuration',return_value={'sealed':True}),\
                patch.object(operator,'root_api',side_effect=read_api),\
                patch.object(operator,'http',return_value={'access_token':'read-token'}) as http,\
                patch.object(operator,'leaf_intent') as leaf:
            # The existing verifier produces the exact completion receipt.
            result = operator.verify({'id':'bootstrap'})
            leaf.side_effect = AssertionError('no mutation intent')
            token = recovery._observation.set(('managed','root','bootstrap'))
            try:
                with patch.object(recovery,'guard_observation'):
                    self.assertEqual(operator.observe_completed_verify({'id':'bootstrap'}),result['evidence_ref'])
                    self.assertTrue(calls)
                    self.assertTrue(all(c.args[0].full_url.endswith('/oauth/tokens') for c in http.call_args_list))
                    with self.assertRaises(RuntimeError):
                        operator.post(worker.urllib.request.Request(next(iter(endpoints.values()))+'/api/management/v1/principals',data=b'{}'))
                    # Completed proof cannot compensate for missing immutable root material.
                    objects.clear()
                    with self.assertRaises(RuntimeError):
                        operator.observe_completed_verify({'id':'bootstrap'})
                    operator.receipt['provisioning'].clear()
                    with self.assertRaises(RuntimeError):
                        operator.observe_completed_verify({'id':'bootstrap'})
            finally:
                recovery._observation.reset(token)

    def test_bootstrap_verify_seals_provisioning_and_observer_accepts_exact_shapes(self):
        operator = worker.Operator.__new__(worker.Operator)
        operator.phase = 'bootstrap'
        operator.binding = dict(generation_id='frozen-generation')
        operator.receipt = dict(dispatches=[],provisioning={'db':{'consumers':{'issued':'value'}}})
        plan = dict(id='bootstrap-operation')
        configuration = dict(generation_id='frozen-generation')
        with patch.object(operator,'check_receipt'),patch.object(operator,'save'), \
                patch.object(operator,'assert_intent'),patch.object(operator,'candidate_credentials'), \
                patch.object(operator,'verification_configuration',return_value=configuration), \
                patch.object(operator,'provision_databases') as provision:
            result = operator.verify(plan)
            provision.assert_called_once_with(plan,verify_only=True)
            completion = operator.receipt['bootstrap_complete']
            self.assertEqual(set(completion['proof']),{'operation_id','binding','configuration','provisioning_digest'})
            self.assertEqual(completion['proof']['provisioning_digest'],worker.digest(worker.canonical(operator.receipt['provisioning'])))
            self.assertEqual(operator.observe_completed_verify(plan),result['evidence_ref'])
            operator.receipt['provisioning']['db']['consumers']['issued']='tampered'
            with self.assertRaises(RuntimeError):
                operator.observe_completed_verify(plan)
            # Historical receipts remain untouched and retain their original evidence.
            del completion['proof']['provisioning_digest']
            completion['evidence_ref']='bootstrap-prepared:'+worker.digest(worker.canonical(completion['proof']))
            self.assertEqual(operator.observe_completed_verify(plan),completion['evidence_ref'])
            completion['proof']['extra']=True
            with self.assertRaises(RuntimeError):
                operator.observe_completed_verify(plan)

    def admin_generation(self):
        """Upgrade only this temporary fixture; keep all legacy tests on v1."""
        import trino_auth
        import warehouse_operations as core
        value = copy.deepcopy(self.bundle['manifest']['spec'])
        value['version'] = 2
        value['profile']['template_fingerprint'] = generation.profile_fingerprint(version=2)
        self.bundle = generation.render(value)
        generation.write_bundle(self.bundle, self.root/'admin-bundle')
        snapshot = self.root/'admin-snapshot'
        for name, data in self.bundle['files'].items():
            if name.startswith('deploy/'):
                path = snapshot/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        for key, db in self.runtime_backend['databases'].items():
            db['roles']['admin'] = dict(username=trino_auth.scoped_username(key, 'admin'),
                                       password='scoped-admin-password-'+key)
        self.private('runtime-backend', worker.canonical(self.runtime_backend))
        self.config.update(bundle=str(self.root/'admin-bundle'), snapshot=str(snapshot))
        self.private('config', worker.canonical(self.config))
        self.operation['delivery_digest'] = self.bundle['manifest']['delivery_digest']
        core.inspect.return_value.update(self.operation)

    def test_new_generation_mandatory_admin_sql_and_durable_completion(self):
        self.admin_generation()
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=http):
            completed = operator.verify(self.operation)
            proof = operator.receipt['verification_complete']['proof']
            self.assertEqual(proof['admin_schemas'], operator.completed_admin_schemas())
            self.assertEqual(completed['evidence_ref'], 'generation-verified:'+worker.digest(worker.canonical(proof)))
            persisted = json.loads(worker.private_file(operator.receipt_path))
            self.assertEqual(persisted['verification_complete']['proof'], proof)
            for key, db in self.runtime_backend['databases'].items():
                admin = db['roles']['admin']
                posts = [r for r in requests if r.get_method() == 'POST' and r.full_url.endswith('/v1/statement')
                         and r.get_header('X-trino-user') == admin['username']]
                self.assertEqual(len(posts), 1)
                self.assertEqual(posts[0].get_header('Authorization'), 'Basic '+base64.b64encode(
                    (admin['username']+':'+admin['password']).encode()).decode())
                self.assertNotEqual(admin, self.runtime_backend['platform_admin'])
                self.assertEqual(proof['sql'][key+'/admin']['backend_id'], proof['admin_schemas'][0]['backend_id'])
            before = sum(r.full_url.endswith('/v1/statement') for r in requests)
            with patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref': 'snapshot:exact'}):
                self.assertEqual(operator.observe_completed_verify(self.operation), completed['evidence_ref'])
                evidence = operator.reconcile(self.operation, 'verify')
                self.assertEqual((evidence.outcome, evidence.evidence_ref), ('SUCCEEDED', completed['evidence_ref']))
            self.assertEqual(sum(r.full_url.endswith('/v1/statement') for r in requests), before)

    def test_admin_verifier_missing_or_wrong_identity_and_sql_fail_closed(self):
        self.admin_generation()
        original = copy.deepcopy(self.runtime_backend)
        key = next(iter(original['databases']))
        for mismatch in ('missing', 'platform', 'catalog', 'generation', 'rows', 'lost_response'):
            with self.subTest(mismatch=mismatch):
                backend = copy.deepcopy(original)
                db = backend['databases'][key]
                if mismatch == 'missing':
                    db['roles'].pop('admin')
                elif mismatch == 'platform':
                    db['roles']['admin'] = backend['platform_admin']
                elif mismatch in ('catalog', 'generation'):
                    db['sql_catalog' if mismatch == 'catalog' else 'generation_id'] = 'wrong'
                self.private('runtime-backend', worker.canonical(original))
                self.operation['id'] = 'admin-'+mismatch
                operator = self.bound()
                objects, kubectl, http, requests = self.candidate_backend(operator)
                self.private('runtime-backend', worker.canonical(backend))
                if mismatch in ('missing', 'platform', 'catalog', 'generation'):
                    with self.assertRaises(RuntimeError):
                        operator.admin_credentials()
                def response(request, **kwargs):
                    is_admin = request.get_header('X-trino-user') == original['databases'][key]['roles']['admin']['username']
                    if is_admin and mismatch == 'lost_response':
                        requests.append(request)
                        raise RuntimeError('synthetic lost response')
                    page = http(request, **kwargs)
                    if is_admin and mismatch == 'rows':
                        page['data'] = []
                    return page
                with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=response):
                    with self.assertRaises((RuntimeError, ValueError)):
                        operator.verify(self.operation)
                    self.assertNotIn('verification_complete', operator.receipt)
                    if mismatch == 'lost_response':
                        with self.assertRaises(RuntimeError):
                            operator.verify(self.operation)
                        self.assertEqual(sum(r.get_header('X-trino-user') == db['roles']['admin']['username'] for r in requests), 1)

    def test_new_admin_reconciliation_rejects_rehashed_missing_or_incomplete_proof(self):
        self.admin_generation()
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=http):
            operator.verify(self.operation)
        original = copy.deepcopy(operator.receipt)
        key = next(iter(self.runtime_backend['databases']))
        for mismatch in ('proof', 'receipt', 'complete', 'dispatched', 'backend_id', 'next_uri', 'rows', 'user', 'catalog', 'summary_id'):
            with self.subTest(mismatch=mismatch):
                operator.receipt = copy.deepcopy(original)
                proof = operator.receipt['verification_complete']['proof']
                sql = operator.receipt['sql']
                saved = sql[key+'/admin']
                if mismatch == 'proof':
                    proof.pop('admin_schemas')
                elif mismatch == 'receipt':
                    sql.pop(key+'/admin')
                elif mismatch in ('complete', 'dispatched'):
                    saved[mismatch] = False
                elif mismatch == 'backend_id':
                    saved['backend_id'] = ''
                elif mismatch == 'next_uri':
                    saved['next_uri'] = 'https://127.0.0.1:8443/v1/statement/pending'
                elif mismatch == 'rows':
                    saved['rows'].pop()
                elif mismatch in ('user', 'catalog'):
                    for row in saved['rows']:
                        row[0 if mismatch == 'user' else 1] = 'wrong'
                else:
                    proof['admin_schemas'][0]['backend_id'] = 'different'
                # Even a self-consistent private hash cannot replace child proof.
                proof['sql'] = copy.deepcopy(sql)
                operator.receipt['verification_complete']['evidence_ref'] = 'generation-verified:'+worker.digest(worker.canonical(proof))
                with patch.object(operator, 'kubectl', side_effect=kubectl), \
                     patch.object(operator, 'http', side_effect=AssertionError('must not resubmit SQL')), \
                     patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref': 'snapshot:exact'}):
                    self.assertEqual(operator.reconcile(self.operation, 'verify').outcome, 'UNKNOWN')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root/'state'))
        environment.start()
        self.addCleanup(environment.stop)
        self.bundle = generation.render(spec(1))
        generation.write_bundle(self.bundle, self.root/'bundle')
        snapshot = self.root/'snapshot'
        snapshot.mkdir()
        for name, data in self.bundle['files'].items():
            if name.startswith('deploy/'):
                target = snapshot/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        self.workdir = self.root/'tofu'
        self.workdir.mkdir(mode=0o755)
        self.workdir.chmod(0o755)  # explicit fixture mode, independent of host umask
        self.private('terraform-state', worker.canonical(dict(lineage='fixture-lineage', serial=9, resources=[])))
        self.private('plan', b'saved-plan')
        self.private('s3', worker.canonical(dict(endpoint_url='http://127.0.0.1:8333', region='us-east-1',
            access_key_id='operator', secret_access_key='private-test-value')))
        self.private('auth', worker.canonical({'Authorization': 'Basic test', 'X-Shirokuma-Role': 'admin'}))
        self.private('ca.pem', b'fixture-ca-bytes')
        identities = json.loads(self.bundle['files']['identity-public.json'])
        self.candidate = dict(version=1, generation_id=self.bundle['manifest']['generation_id'],
            trino=dict(endpoint_url='https://127.0.0.1:8443', ca_file=str(self.root/'ca.pem')),
            databases={key: dict(polaris=dict(endpoint_url='http://127.0.0.1:8181', client_id='dedicated-'+key,
                client_secret='candidate-secret'), trino=dict(username=identities['databases'][key]['roles']['data_engineer'],
                password='candidate-password')) for key in self.bundle['tofu']['databases']})
        self.private('candidate', worker.canonical(self.candidate))
        self.config = dict(version=1, bundle=str(self.root/'bundle'), snapshot=str(snapshot),
            tofu_plan=str(self.root/'plan'), tofu_workdir=str(self.workdir), kube_context=local_gitops.CONTEXT,
            tofu_state=str(self.root/'terraform-state'), state_lineage='fixture-lineage', state_serial=9,
            candidate_credentials=str(self.root/'candidate'),
            s3_credentials=str(self.root/'s3'), auth_headers=str(self.root/'auth'))
        (self.root/'state').mkdir()
        self.runtime_backend = dict(version=2, platform_admin=dict(username='shirokuma_admin', password='admin-password'),
            databases={key: dict(sql_catalog=db['sql_catalog'], generation_id=self.bundle['manifest']['generation_id'],
                roles={role: dict(username=user, password='candidate-password' if role == 'data_engineer' else 'viewer-password')
                       for role, user in identities['databases'][key]['roles'].items()})
                for key, db in self.bundle['tofu']['databases'].items()})
        self.private('runtime-backend', worker.canonical(self.runtime_backend))
        self.private('runtime-catalog', worker.canonical(dict(version=1, databases={key: dict(
            service_ref=db['service_ref'], realm=db['realm'], catalog=db['catalog'],
            generation_id=self.bundle['manifest']['generation_id'],
            client_id=self.candidate['databases'][key]['polaris']['client_id'], client_secret='candidate-secret')
            for key, db in self.bundle['tofu']['databases'].items()})))
        self.private('runtime-accounts', worker.canonical(dict(admin=dict(role='admin', platform_admin=True,
            salt='12'*16, password_hash='34'*32))))
        self.private('runtime-client', worker.canonical(dict(username='admin', password='test', selected_role='admin')))
        self.runtime_profile = dict(version=1, mode='database', state_dir=str(self.root/'state'),
            auth_file=str(self.root/'runtime-accounts'), trino_auth_file=str(self.root/'runtime-backend'),
            catalog_auth_file=str(self.root/'runtime-catalog'), trino_ca_file=str(self.root/'ca.pem'),
            client_auth_file=str(self.root/'runtime-client'), sql_endpoint_url='http://127.0.0.1:8080')
        self.private('runtime.json', worker.canonical(self.runtime_profile))
        self.config.update(runtime_profile=str(self.root/'runtime.json'), runtime_endpoint='http://127.0.0.1:8888')
        self.path = self.private('config', worker.canonical(self.config))
        self.operation = dict(id='op', actor='admin', generation_id=self.bundle['manifest']['generation_id'],
                              delivery_digest=self.bundle['manifest']['delivery_digest'],
                              secret_observations=[])
        self.intent_patch = patch('warehouse_operations.inspect', return_value=dict(self.operation,
            steps=[dict(step=s, state='RUNNING') for s in worker.STEPS+('verify',)]))
        self.intent_patch.start()
        self.addCleanup(self.intent_patch.stop)

    def private(self, name, data):
        path = self.root/name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def bound(self):
        operator = worker.Operator(self.path)
        operator.bind(self.operation)
        operator.check_receipt(self.operation)
        return operator

    def test_private_permissions_and_all_bound_artifact_drift(self):
        operator = self.bound()
        self.assertEqual(operator.receipt_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(operator.receipt_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(operator.receipt_path, self.root/'state/warehouse-operator-receipts/op.json')
        for name in ('plan', 's3', 'auth', 'candidate', 'ca.pem'):
            path = self.root/name
            before = path.read_bytes()
            path.write_bytes(before+b' ')
            with patch.object(operator, 'command') as command, self.assertRaises(ValueError):
                operator.check_receipt(self.operation)
            command.assert_not_called()
            path.write_bytes(before)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            worker.Operator(self.path)

    def test_shared_operation_receipt_idempotence_and_conflicting_config_rejected(self):
        operator = self.bound()
        before = operator.receipt_path.read_bytes()
        worker.Operator(self.path).bind(self.operation)
        self.assertEqual(operator.receipt_path.read_bytes(), before)
        other = self.root/'different-config-directory'
        other.mkdir(mode=0o700)
        config = other/'config'
        config.write_bytes(self.path.read_bytes())
        config.chmod(0o600)
        with self.assertRaises(ValueError):
            worker.Operator(config).bind(self.operation)
        self.private('plan', b'different-private-plan')
        with self.assertRaises(ValueError):
            worker.Operator(self.path).bind(self.operation)
        self.assertEqual(operator.receipt_path.read_bytes(), before)

    def test_prepared_replan_validates_private_predecessor_against_own_old_ledger(self):
        import warehouse_operations as core
        operator = self.bound()
        operator.predecessor_id = 'old-bootstrap'
        binding = dict(operator.binding, phase='bootstrap', predecessor_id=None)
        proof = dict(operation_id='old-bootstrap', binding=binding,
                     configuration=dict(generation_id=self.bundle['manifest']['generation_id']))
        completion = dict(proof=proof, evidence_ref='bootstrap-prepared:'+worker.digest(worker.canonical(proof)))
        receipt = dict(actor='admin', binding=binding, bootstrap_complete=completion,
                       provisioning={'retained-principal': {'client_secret': 'private-retained'}})
        previous_path = operator.receipt_path.parent/'old-bootstrap.json'
        worker.save_private(previous_path, receipt)
        prior = dict(id='old-bootstrap', actor='admin', phase='bootstrap', state='PREPARED',
            generation_id=self.bundle['manifest']['generation_id'], delivery_digest=self.bundle['manifest']['delivery_digest'],
            manifest=self.bundle['manifest'], steps=[dict(step='verify', state='SUCCEEDED', evidence_ref=completion['evidence_ref'])])
        changed = copy.deepcopy(self.bundle['manifest']['spec'])
        changed['generation_id'] = 'new-generation-after-drift'
        changed['databases']['opaque_0']['binding']['desired_revision'] = 2
        new_plan = dict(actor='admin', manifest=generation.render(changed)['manifest'])
        with patch.object(core, 'inspect', return_value=prior) as inspect:
            self.assertEqual(operator.prepared_predecessor(new_plan)['provisioning'], receipt['provisioning'])
            inspect.assert_called_once_with('old-bootstrap', actor='admin')
            receipt['binding']['delivery_digest'] = new_plan['manifest']['delivery_digest']
            worker.save_private(previous_path, receipt)
            with self.assertRaisesRegex(RuntimeError, 'original ledger'):
                operator.prepared_predecessor(new_plan)

    def test_source_and_children_use_foundation_ssa_manager_without_force(self):
        operator = self.bound()
        source = dict(apiVersion='source.toolkit.fluxcd.io/v1', kind='Bucket',
            metadata=dict(name=local_gitops.SOURCE, namespace='flux-system', uid='retained-source'),
            spec=dict(bucketName='retained-public-source', prefix='previous', secretRef={'name': 'previous'}))
        def get(*args, **kwargs):
            if args[0] == 'apply' or 'wait' in args:
                return ''
            if args[3] == 'kustomization.kustomize.toolkit.fluxcd.io':
                return json.dumps(dict(spec={'suspend': True}))
            return json.dumps(source)
        with patch.object(operator, 'kubectl', side_effect=get) as kubectl, patch.object(operator, 'observe_source'):
            operator.switch_source()
            apply = next(call for call in kubectl.call_args_list if call.args[0] == 'apply')
            self.assertIn('--field-manager=shirokuma-local-gitops', apply.args)
            self.assertNotIn('--force-conflicts', apply.args)
            self.assertEqual(yaml.safe_load(apply.kwargs['payload'])['spec']['bucketName'], 'retained-public-source')
            self.assertEqual(operator.receipt['source_uid'], 'retained-source')
        child = operator.children[0]
        actual = copy.deepcopy(child)
        actual['metadata']['uid'] = 'retained-child'
        actual['metadata']['resourceVersion'] = '7'
        actual['spec']['suspend'] = True
        name = child['metadata']['name']
        events = []
        def deliver(*args, **kwargs):
            if args[0] == 'apply':
                self.assertIn('--field-manager=shirokuma-local-gitops', args)
                self.assertNotIn('--force-conflicts', args)
                documents = list(yaml.safe_load_all(kwargs['payload']))
                self.assertEqual(len(documents), 1)
                self.assertEqual(documents[0]['spec'], dict(child['spec'], suspend=True))
                self.assertIs(actual['spec']['suspend'], True)
                actual['spec'] = documents[0]['spec']
                events.append('apply-suspended')
            elif 'patch' in args:
                self.assertEqual(events, ['apply-suspended'])
                self.assertIn('--field-manager=shirokuma-local-gitops', args)
                self.assertIn(name+':False', operator.receipt['child_dispatches'])
                self.assertIn(name+':False', json.loads(operator.receipt_path.read_bytes())['child_dispatches'])
                self.assertEqual(json.loads(args[-1]), [
                    {'op': 'test', 'path': '/metadata/uid', 'value': 'retained-child'},
                    {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '7'},
                    {'op': 'add', 'path': '/spec/suspend', 'value': False},
                ])
                actual['spec']['suspend'] = False
                events.append('resume')
            elif 'wait' in args:
                self.assertEqual(events, ['apply-suspended', 'resume'])
                events.append('ready')
            return ''
        def observe(desired):
            self.assertEqual(events, ['apply-suspended', 'resume', 'ready'])
            self.assertEqual(actual['spec'], desired['spec'])
            events.append('observe')
            return 'retained-child'
        # Repeat for a later configuration on the same retained child.
        for revision in range(2):
            with self.subTest(revision=revision):
                child = copy.deepcopy(child)
                child['spec']['path'] += '/next'
                actual['spec']['suspend'] = True
                operator.receipt['children_before'][name] = dict(uid='retained-child', spec=copy.deepcopy(actual['spec']))
                operator.receipt['child_dispatches'] = []
                operator.receipt['dispatches'] = ['reconcile_storage']
                operator.save()
                events.clear()
                with patch.object(operator, 'get_child', side_effect=lambda desired: copy.deepcopy(actual)), \
                        patch.object(operator, 'observe_child', side_effect=observe), \
                        patch.object(operator, 'kubectl', side_effect=deliver):
                    operator.apply_children([child], plan=self.operation, step='reconcile_storage')
                self.assertIs(child['spec']['suspend'], False)
                self.assertEqual(events, ['apply-suspended', 'resume', 'ready', 'observe'])
    def test_two_process_binds_cannot_overwrite_one_shared_operation_receipt(self):
        other = self.root/'other'
        other.mkdir(mode=0o700)
        other_plan = other/'plan'
        other_plan.write_bytes(b'different-saved-plan')
        other_plan.chmod(0o600)
        other_config = other/'config'
        other_config.write_bytes(worker.canonical(dict(self.config, tofu_plan=str(other_plan))))
        other_config.chmod(0o600)
        source = """
import json, sys
sys.path.insert(0, sys.argv[1])
import warehouse_operator
try:
    warehouse_operator.Operator(sys.argv[2]).bind(json.loads(sys.argv[3]))
except ValueError:
    print('REJECTED')
else:
    print('BOUND')
"""
        processes = [subprocess.Popen(['rtk', 'proxy', sys.executable, '-c', source,
            str(ROOT/'tools/local_experiment'), str(path), json.dumps(self.operation)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for path in (self.path, other_config)]
        outputs = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stderr)
                outputs.append(stdout.strip())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.assertEqual(sorted(outputs), ['BOUND', 'REJECTED'])
        path = self.root/'state/warehouse-operator-receipts/op.json'
        winner = self.path if outputs[0] == 'BOUND' else other_config
        self.assertEqual(json.loads(worker.private_file(path))['binding'], worker.Operator(winner).binding)
        before = path.read_bytes()
        worker.Operator(winner).bind(self.operation)
        self.assertEqual(path.read_bytes(), before)

    def test_reconcile_observer_not_called_when_busy_and_receives_current_snapshot(self):
        import warehouse_operations as core
        args = argparse.Namespace(config=self.path, command='reconcile', operation_id='op', step='apply_secrets')
        principal = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        @contextlib.contextmanager
        def busy():
            raise core.OperationError('WAREHOUSE_OPERATOR_BUSY')
            yield
        with patch.object(worker.Operator, 'principal', return_value=principal), \
                patch.object(core, '_operator_gate', busy), patch.object(worker.Operator, 'reconcile') as observer:
            with self.assertRaises(core.OperationError) as error:
                worker.run(args)
            self.assertEqual(error.exception.code, 'WAREHOUSE_OPERATOR_BUSY')
            observer.assert_not_called()
        current = dict(self.operation, revision=77, state='UNKNOWN')
        evidence = core.ReconciliationEvidence('apply_secrets', 'UNKNOWN', 'observation:unavailable', 'observed_configuration')
        with patch.object(worker.Operator, 'principal', return_value=principal), \
                patch.object(core, 'inspect', return_value=current), \
                patch.object(worker.Operator, 'reconcile', return_value=evidence) as observer, \
                patch.object(core, '_reconcile_locked', return_value={'state': 'UNKNOWN'}) as commit:
            self.assertEqual(worker.run(args), {'state': 'UNKNOWN'})
            observer.assert_called_once_with(current, 'apply_secrets')
            self.assertIs(observer.call_args.args[0], current)
            commit.assert_called_once_with('op', actor='admin', evidence=evidence, is_admin=True)

    def test_public_snapshot_and_config_drift_reject_before_dispatch(self):
        operator = self.bound()
        target = next((self.root/'snapshot').rglob('deployment.yaml'))
        target.write_bytes(target.read_bytes()+b'\n')
        with self.assertRaises(ValueError):
            operator.check_receipt(self.operation)
        self.config['kube_context'] = 'other-cluster'
        self.path.write_bytes(worker.canonical(self.config))
        with self.assertRaises(ValueError):
            worker.Operator(self.path)

    def test_current_auth_rechecked_no_fake_admin_flag(self):
        operator = self.bound()
        principal = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        with patch.object(request_auth, 'authenticate_principal', return_value=principal) as authenticate:
            self.assertTrue(operator.authorize('admin'))
            authenticate.assert_called_once_with({'Authorization': 'Basic test', 'X-Shirokuma-Role': 'admin'})
        with patch.object(request_auth, 'authenticate_principal', return_value=request_auth.AuthenticatedPrincipal('admin', 'admin', False)):
            with self.assertRaises(PermissionError):
                operator.authorize('admin')

    def test_saved_plan_apply_once_and_response_loss_is_unknown(self):
        operator = self.bound()
        with patch.object(operator, 'validate_saved_plan'), patch.object(operator, 'suspend_children'), \
                patch.object(worker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('tofu', 180)) as run:
            with self.assertRaises(RuntimeError):
                operator.execute_step('apply_secrets', self.operation)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['rtk', 'proxy', 'tofu', 'apply', '-input=false', '-no-color',
                '-state='+str(self.root/'terraform-state'), str(self.root/'plan')])
            self.assertNotIn('apply_secrets', operator.receipt['acknowledgements'])
        with patch.object(operator, 'observe_secret_apply', side_effect=RuntimeError('insufficient')):
            evidence = operator.reconcile(self.operation, 'apply_secrets')
        self.assertEqual(evidence.outcome, 'UNKNOWN')
        self.assertEqual(evidence.basis, 'observed_configuration')
        with patch.object(worker.subprocess, 'run') as run, self.assertRaises(RuntimeError):
            operator.execute_step('apply_secrets', self.operation)
        run.assert_not_called()
        # A distinct operation can receive a confirmed backend acknowledgement.
        second = dict(self.operation, id='second-operation')
        operator.bind(second)
        operator.check_receipt(second)
        with patch.object(operator, 'validate_saved_plan'), patch.object(operator, 'suspend_children'), \
                patch.object(operator, 'observe_secrets', return_value=[]), \
                patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='private-output')):
            result = operator.execute_step('apply_secrets', second)
        self.assertNotIn('private-output', json.dumps(result))
        self.assertEqual(operator.reconcile(second, 'apply_secrets').outcome, 'SUCCEEDED')
        self.assertEqual(operator.reconcile(second, 'reconcile_trino').outcome, 'UNKNOWN')

    def test_new_config_passes_phase_data_directory_to_actual_apply_subprocess(self):
        self.config['tofu_data_dir'] = str(self.workdir/'.terraform')
        self.private('config',worker.canonical(self.config))
        with patch.dict(os.environ,TF_DATA_DIR='/sentinel/shared'):
            parent = dict(os.environ)
            operator = self.bound()
            with patch.object(operator,'validate_saved_plan'),patch.object(operator,'suspend_children'),\
                    patch.object(operator,'observe_secrets',return_value=[]),\
                    patch.object(worker.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='')) as run:
                operator.execute_step('apply_secrets',self.operation)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][2:4],['tofu','apply'])
            self.assertEqual(run.call_args.kwargs['env'],dict(parent,TF_DATA_DIR=self.config['tofu_data_dir']))
            self.assertEqual(dict(os.environ),parent)

    def test_saved_plan_rejects_nonsecret_mutation_and_context_drift(self):
        operator = self.bound()
        document = dict(configuration={'provider_config': {'kubernetes': {'expressions': {
            'config_context': {'references': ['var.kube_context']}}}}},
            variables={'kube_context': {'value': local_gitops.CONTEXT}}, resource_changes=[])
        with patch.object(operator, 'command', side_effect=lambda *a, **k: json.dumps(document)) as command:
            operator.validate_saved_plan()
            self.assertEqual(command.call_args.args[0], ['rtk', 'proxy', 'tofu', 'show', '-json', str(self.root/'plan')])
            document['resource_changes'] = [{'type': 'postgresql_database', 'change': {'actions': ['create']}}]
            with self.assertRaises(ValueError):
                operator.validate_saved_plan()
            document['resource_changes'] = []
            document['variables']['kube_context']['value'] = 'unapproved'
            with self.assertRaises(ValueError):
                operator.validate_saved_plan()

    def test_explicit_state_identity_serial_and_same_serial_content_drift(self):
        operator = self.bound()
        self.assertEqual(self.workdir.stat().st_mode & 0o777, 0o755)
        path = self.root/'terraform-state'
        for changed in (dict(lineage='other', serial=9), dict(lineage='fixture-lineage', serial=10),
                        dict(lineage='fixture-lineage', serial=9, resources=['changed'])):
            path.write_bytes(worker.canonical(changed))
            with patch.object(operator, 'command') as command, self.assertRaises(ValueError):
                operator.check_state_before_apply()
            command.assert_not_called()
        path.unlink()
        with self.assertRaises(OSError):
            operator.check_state_before_apply()

    def test_saved_plan_metadata_updates_versioned_replacement_and_data_rejection(self):
        operator = self.bound()
        ref = self.bundle['tofu']['bootstrap_secret_refs'][0]
        before = dict(metadata=[dict(namespace=ref['namespace'], name=ref['name'],
            annotations={worker.VERSION_ANNOTATION: ref['version'], generation.ANNOTATION: 'old-generation'})],
            immutable=True, data={key: 'fixture-secret' for key in ref['keys']})
        after = copy.deepcopy(before)
        after['metadata'][0]['annotations'][generation.ANNOTATION] = self.operation['generation_id']
        resource = dict(type='kubernetes_secret_v1', provider_name='registry.opentofu.org/hashicorp/kubernetes',
                        change=dict(actions=['update'], before=before, after=after))
        document = dict(configuration={'provider_config': {'kubernetes': {'expressions': {
            'config_context': {'constant_value': local_gitops.CONTEXT}}}}}, resource_changes=[resource])
        with patch.object(operator, 'command', side_effect=lambda *a, **k: json.dumps(document)):
            operator.validate_saved_plan()
            after['data'][ref['keys'][0]] = 'changed-same-name'
            with self.assertRaises(ValueError):
                operator.validate_saved_plan()
            after['data'] = dict(before['data'])
            before['metadata'][0].update(name='previous-v0', annotations={worker.VERSION_ANNOTATION: 'v0'})
            resource['change']['actions'] = ['delete', 'create']
            operator.validate_saved_plan()
            before['metadata'][0]['annotations'][worker.VERSION_ANNOTATION] = ref['version']
            with self.assertRaises(ValueError):
                operator.validate_saved_plan()

    def candidate_backend(self, operator, *, actual_runtime=False):
        """Only generated K8 documents and small protocol responses; never executes a client."""
        objects = {}
        if not actual_runtime:
            operator.verify_runtime_handoff = Mock()
        from tests.test_database_provision import API
        import database_provision
        apis = {}
        for key, entry in self.bundle['tofu']['databases'].items():
            api, state = API(), {}
            issued = database_provision.provision(api, key, entry, generation.SCHEMAS, state, lambda value: None)
            for consumer, credential in issued.items():
                principal = api.entities[database_provision.permissions.MANAGEMENT+'/principals/'+credential['principal_name']]
                if consumer == 'trino':
                    principal['clientId'] = self.candidate['databases'][key]['polaris']['client_id']
            apis[entry['realm']] = api
        operator.root_api = lambda endpoint, realm, credentials: apis[realm]
        self.apis = apis
        def add(obj, namespace='shirokuma-dev'):
            obj = copy.deepcopy(obj)
            meta = obj['metadata']
            meta.setdefault('namespace', namespace)
            meta.update(uid='uid-'+meta['name'], resourceVersion='17', generation=2)
            objects[(obj['kind'].lower(), meta['namespace'], meta['name'])] = obj
            return obj
        for desired in operator.children:
            obj = add(desired)
            obj['status'] = {'conditions': [{'type': 'Ready', 'status': 'True', 'observedGeneration': 2}]}
            operator.receipt['children_before'][obj['metadata']['name']] = dict(uid=obj['metadata']['uid'], spec=obj['spec'])
            for health in desired['spec'].get('healthChecks', []):
                obj = add(operator.expected_resource(health), health['namespace'])
                obj['status'] = dict(observedGeneration=2, readyReplicas=obj.get('spec', {}).get('replicas', 1),
                    updatedReplicas=obj.get('spec', {}).get('replicas', 1), currentRevision='same', updateRevision='same',
                    conditions=[{'type': 'Complete', 'status': 'True'}])
        for path, content in operator.files.items():
            if path.endswith(('.yaml', '.yml')):
                obj = yaml.safe_load(content)
                if isinstance(obj, dict) and obj.get('kind') == 'ConfigMap':
                    add(obj)
            if Path(path).name != 'kustomization.yaml':
                continue
            document = yaml.safe_load(content)
            for config in document.get('configMapGenerator', []):
                data = {}
                for item in config.get('files', []):
                    key, filename = item.split('=', 1) if '=' in item else (Path(item).name, item)
                    data[key] = operator.files[posixpath.normpath(posixpath.join(posixpath.dirname(path), filename))].decode()
                add(dict(kind='ConfigMap', metadata={'name': config['name']}, data=data))
        ref = self.bundle['manifest']['spec']['shared_secret_refs']['gitops_source']
        source = add(dict(kind='Bucket', metadata=dict(name=local_gitops.SOURCE, namespace='flux-system'),
            spec=dict(prefix=operator.prefix, secretRef={'name': ref['name']}),
            status={'conditions': [{'type': 'Ready', 'status': 'True', 'observedGeneration': 2}]}))
        operator.receipt['source_uid'] = source['metadata']['uid']
        refs = self.bundle['tofu']['bootstrap_secret_refs']+self.bundle['tofu']['deferred_secret_refs']
        for ref in refs:
            values = {key: 'fixture-secret-value' for key in ref['keys']}
            for key, db in self.bundle['tofu']['databases'].items():
                if ref == db['trino_secret']:
                    values.update(client_id=self.candidate['databases'][key]['polaris']['client_id'],
                                  client_secret='candidate-secret', realm=db['realm'])
                if ref == db['root_secret']:
                    values.update(client_id='root', realm=db['realm'])
                if ref == db['bootstrap_secret']:
                    issued_name = 'shirokuma_'+__import__('hashlib').sha256(key.encode()).hexdigest()[:16]+'_iceberg_bootstrap'
                    values.update(client_id='id-'+issued_name, client_secret='private-secret', realm=db['realm'])
            add(dict(kind='Secret', metadata=dict(name=ref['name'], namespace=ref['namespace'],
                annotations={worker.VERSION_ANNOTATION: ref['version'], generation.ANNOTATION: self.operation['generation_id']}),
                immutable=True, data={k: base64.b64encode(v.encode()).decode() for k, v in values.items()}))
        def kubectl(*args, **kwargs):
            self.assertEqual(args[2], 'get')
            return json.dumps(objects[(args[3].split('.')[0].lower(), args[1], args[4])])
        with patch.object(operator, 'kubectl', side_effect=kubectl):
            self.operation['secret_observations'] = operator.observe_secrets(self.operation)
        operator.receipt['secret_after'] = self.operation['secret_observations']
        operator.save()
        requests = []
        def http(request, **kwargs):
            requests.append(request)
            path = unquote(urlsplit(request.full_url).path)
            if path == '/api/catalog/v1/oauth/tokens':
                return {'access_token': 'candidate-token'}
            if path.startswith('/api/management/v1/catalogs/'):
                entry = next(e for e in self.bundle['tofu']['databases'].values() if path.endswith('/'+e['catalog']))
                return {'catalog': generation._catalog(entry)}
            if path.endswith('/namespaces'):
                return {'namespaces': [[name] for name in generation.SCHEMAS]}
            if '/namespaces/' in path:
                return {'namespace': [path.rsplit('/', 1)[1]], 'properties': {}}
            self.assertEqual(path, '/v1/statement')
            self.assertEqual(request.get_method(), 'POST')
            key = next(k for k, e in self.bundle['tofu']['databases'].items()
                       if '"'+e['sql_catalog']+'".information_schema' in request.data.decode())
            return dict(id='fixture-query-'+key, columns=[{'name': n} for n in ('verified_user', 'catalog_name', 'schema_name')],
                data=[[request.get_header('X-trino-user'), self.bundle['tofu']['databases'][key]['sql_catalog'], name]
                      for name in generation.SCHEMAS])
        return objects, kubectl, http, requests

    def test_actual_verifier_matching_candidate_and_newly_created_secrets(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        # Initial absent descriptors legitimately become the identities captured after apply.
        self.operation['secret_observations'] = [dict(r, exists=False, uid=None, resource_version=None, immutable=None)
                                                 for r in self.operation['secret_observations']]
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=http):
            result = operator.verify(self.operation)
            self.assertTrue(result['verified'])
            self.assertNotIn('admin_schemas', operator.receipt['verification_complete']['proof'])
            self.assertFalse(any(key.endswith('/admin') for key in operator.receipt['sql']))
            self.assertNotIn('candidate-secret', json.dumps(result))
            self.assertTrue(operator.verify(self.operation)['verified'])
        self.assertEqual(operator.verify_runtime_handoff.call_count, 2)
        self.assertEqual(sum(r.get_method() == 'POST' and '/v1/statement' in r.full_url for r in requests), 2)
        self.assertTrue(all(r.get_method() == 'GET' for r in requests if '/api/management/' in r.full_url))

    def delivery_backend(self, operator, objects, read, *, on_wait=None):
        """Synthetic API writes; keep the production apply, CAS, wait and observation paths."""
        for child in operator.children:
            obj = objects[('kustomization', 'flux-system', child['metadata']['name'])]
            obj['spec']['suspend'] = True
        def kubectl(*args, **kwargs):
            if args[0] == 'apply':
                for child in yaml.safe_load_all(kwargs['payload']):
                    obj = objects[('kustomization', child['metadata']['namespace'], child['metadata']['name'])]
                    obj['spec'] = child['spec']
                return ''
            if args[2] == 'patch':
                obj = objects[('kustomization', args[1], args[3].split('/')[1])]
                actions = json.loads(args[-1])
                self.assertEqual(actions[:2], [
                    dict(op='test', path='/metadata/uid', value=obj['metadata']['uid']),
                    dict(op='test', path='/metadata/resourceVersion', value=obj['metadata']['resourceVersion'])])
                obj['spec']['suspend'] = actions[2]['value']
                return ''
            if args[2] == 'wait':
                self.assertIn('--timeout=90s', args)
                if on_wait:
                    on_wait(args[3].split('/')[1])
                return ''
            return read(*args, **kwargs)
        return kubectl

    def test_delivery_reads_each_health_once_after_capture_before_next_child(self):
        operator = self.bound()
        objects, read, _, _ = self.candidate_backend(operator)
        kubectl = self.delivery_backend(operator, objects, read)
        with patch.object(operator, 'kubectl', side_effect=kubectl) as calls, \
                patch.object(operator, 'switch_source'), \
                patch.object(operator, 'observe_health', wraps=operator.observe_health) as health:
            for step in worker.STEPS[2:]:
                expected = [h for child in operator.group(step) for h in child['spec'].get('healthChecks', [])]
                health.reset_mock()
                calls.reset_mock()
                operator.execute_step(step, self.operation)
                self.assertEqual([c.args[0] for c in health.call_args_list], expected)
                for h in expected:
                    reads = [c for c in calls.call_args_list
                             if c.args[:5] == ('-n', h['namespace'], 'get', h['kind'], h['name'])]
                    # Identity capture plus one strict health read, formerly three GETs.
                    self.assertEqual(len(reads), 2)
                    self.assertIn('--ignore-not-found', reads[0].args)
                    self.assertNotIn('--ignore-not-found', reads[1].args)
                self.assertIn(step, operator.receipt['acknowledgements'])
                # Each child must be strictly observed before delivery of the next dependency.
                events = calls.call_args_list
                applies = [i for i, c in enumerate(events) if c.args[0] == 'apply']
                for index, child in enumerate(operator.group(step)):
                    end = applies[index+1] if index+1 < len(applies) else len(events)
                    for h in child['spec'].get('healthChecks', []):
                        self.assertTrue(any(c.args[:5] == ('-n', h['namespace'], 'get', h['kind'], h['name'])
                                            and '--ignore-not-found' not in c.args
                                            for c in events[applies[index]:end]))

    def test_configuration_proof_reuses_only_current_strict_health_observations(self):
        operator = self.bound()
        objects, kubectl, _, _ = self.candidate_backend(operator)
        expected_children = [dict(name=c['metadata']['name'], uid='uid-'+c['metadata']['name'], health=[
            dict(kind=h['kind'], namespace=h['namespace'], name=h['name'], uid='uid-'+h['name'], generation=2)
            for h in c['spec'].get('healthChecks', [])]) for c in operator.children]
        expected_health = [h for c in operator.children for h in c['spec'].get('healthChecks', [])]
        with patch.object(operator, 'kubectl', side_effect=kubectl) as calls, \
                patch.object(operator, 'observe_health', wraps=operator.observe_health) as health:
            for _ in range(2):
                health.reset_mock()
                calls.reset_mock()
                proof = operator.verification_configuration(self.operation)
                self.assertEqual(proof, dict(generation_id=operator.binding['generation_id'],
                    source_uid='uid-'+local_gitops.SOURCE, children=expected_children,
                    secrets=operator.receipt['secret_after']))
                self.assertEqual([c.args[0] for c in health.call_args_list], expected_health)
                for h in expected_health:
                    self.assertEqual(sum(c.args[:5] == ('-n', h['namespace'], 'get', h['kind'], h['name'])
                                         for c in calls.call_args_list), 1)
            h = next(h for h in expected_health if h['kind'] == 'Deployment')
            objects[(h['kind'].lower(), h['namespace'], h['name'])]['status']['readyReplicas'] = 0
            with self.assertRaisesRegex(RuntimeError, 'health not observed'):
                operator.verification_configuration(self.operation)

    def test_delivery_and_configuration_reject_bad_current_child_and_health(self):
        for path in ('delivery', 'configuration'):
            for fault in ('health', 'uid', 'config', 'generation', 'child_uid', 'child_config', 'child_generation'):
                with self.subTest(path=path, fault=fault):
                    operator = self.bound()
                    operator.receipt['dispatches'] = []
                    operator.receipt['child_dispatches'] = []
                    operator.receipt['acknowledgements'] = {}
                    objects, read, _, _ = self.candidate_backend(operator)
                    child = next(c for c in operator.children if any(
                        h['kind'] == 'Deployment' for h in c['spec'].get('healthChecks', [])))
                    h = next(h for h in child['spec']['healthChecks'] if h['kind'] == 'Deployment')
                    obj = objects[(h['kind'].lower(), h['namespace'], h['name'])]
                    operator.receipt['resource_uids']['/'.join((h['kind'], h['namespace'], h['name']))] = obj['metadata']['uid']
                    def corrupt(name=None):
                        if name is not None and name != child['metadata']['name']:
                            return
                        if fault == 'health':
                            obj['status']['readyReplicas'] = 0
                        elif fault == 'uid':
                            obj['metadata']['uid'] = 'replacement'
                        elif fault == 'config':
                            obj['spec']['replicas'] = 0
                        elif fault == 'generation':
                            obj['metadata']['annotations'][generation.ANNOTATION] = 'other'
                        else:
                            actual = objects[('kustomization', 'flux-system', child['metadata']['name'])]
                            if fault == 'child_uid':
                                actual['metadata']['uid'] = 'replacement'
                            elif fault == 'child_config':
                                actual['spec']['path'] = './wrong'
                            else:
                                actual['status']['conditions'][0]['observedGeneration'] = 1
                    if path == 'delivery':
                        kubectl = self.delivery_backend(operator, objects, read, on_wait=corrupt)
                    else:
                        corrupt()
                        kubectl = read
                    with patch.object(operator, 'kubectl', side_effect=kubectl), self.assertRaises(RuntimeError):
                        if path == 'delivery':
                            step = next(s for s in worker.STEPS[2:] if child in operator.group(s))
                            operator.execute_step(step, self.operation)
                        else:
                            operator.verification_configuration(self.operation)
                    self.assertEqual(operator.receipt['acknowledgements'], {})

    def test_secret_continuity_fallback_never_overrides_present_private_baseline(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        before = operator.receipt_path.read_bytes()
        with patch.object(operator, '_reconciled_secret_after', side_effect=AssertionError('private baseline wins')) as fallback, \
                patch.object(operator, 'kubectl', side_effect=kubectl):
            operator.verify_secret_identities(self.operation)
            # A present empty baseline retains the existing initial-baseline path.
            operator.receipt['secret_after'] = []
            operator.verify_secret_identities(self.operation)
            for value in (None, 'malformed', [{}]):
                operator.receipt['secret_after'] = value
                with self.subTest(value=type(value).__name__), self.assertRaises((TypeError, KeyError)):
                    operator.verify_secret_identities(self.operation)
            operator.receipt['secret_after'] = []
            self.operation['secret_observations'][0].update(exists=False,uid=None,resource_version=None,immutable=None)
            with self.assertRaises(RuntimeError):
                operator.verify_secret_identities(self.operation)
            fallback.assert_not_called()
        self.assertEqual(operator.receipt_path.read_bytes(), before)

    def test_legacy_full_without_private_after_uses_initial_uids_for_normal_proof(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        del operator.receipt['secret_after']
        # Ineligible canonical material must not replace the original identities.
        self.continuity_database(operator)
        with patch.object(operator, 'validate_saved_plan', side_effect=AssertionError('normal proof has no fallback')) as validate, \
                patch.object(operator, 'kubectl', side_effect=kubectl):
            operator.verify_secret_identities(self.operation)
            first = self.operation['secret_observations'][0]
            obj = objects[('secret',first['namespace'],first['name'])]
            obj['metadata']['uid'] = 'recreated'
            with self.assertRaises(RuntimeError):
                operator.verify_secret_identities(self.operation)
            obj['metadata']['uid'] = first['uid']
            first.update(exists=False,uid=None,resource_version=None,immutable=None)
            with self.assertRaises(RuntimeError):
                operator.verify_secret_identities(self.operation)
            validate.assert_not_called()
        self.assertNotIn('secret_after', operator.receipt)

    def continuity_database(self, operator, *, missing_column=False):
        path = operator.receipt_path.parent.parent/'jobs.sqlite3'
        with contextlib.closing(worker.sqlite3.connect(path)) as db:
            db.executescript('''CREATE TABLE operations (id TEXT, actor TEXT);
                CREATE TABLE operation_requests (operation_id TEXT,actor TEXT,generation_id TEXT,
                    phase TEXT,predecessor_id TEXT);
                CREATE TABLE generation_plans (generation_id TEXT,delivery_digest TEXT,manifest TEXT);
                CREATE TABLE operation_steps (operation_id TEXT,step TEXT,state TEXT,evidence_ref TEXT);''')
            if not missing_column:
                db.execute('ALTER TABLE operation_requests ADD COLUMN secret_after TEXT')
                db.execute('INSERT INTO operations VALUES (?,?)',('op','admin'))
                db.execute('INSERT INTO operation_requests VALUES (?,?,?,?,?,?)',
                    ('op','admin',self.operation['generation_id'],'full',None,'malformed unused baseline'))
                db.execute('INSERT INTO generation_plans VALUES (?,?,?)',
                    (self.operation['generation_id'],self.operation['delivery_digest'],json.dumps(self.bundle['manifest'])))
                db.execute('INSERT INTO operation_steps VALUES (?,?,?,?)',
                    ('op','apply_secrets','SUCCEEDED','operator-ack:normal'))
            db.commit()
        return path

    def test_continuity_metadata_is_read_only_and_legacy_stores_are_unchanged(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        del operator.receipt['secret_after']
        path = operator.receipt_path.parent.parent/'jobs.sqlite3'
        native_connect = worker.sqlite3.connect
        connections = []
        statements = []
        class ReadOnly(worker.sqlite3.Connection):
            closed = False
            def commit(self):
                raise AssertionError('metadata lookup must never commit')
            def close(self):
                self.closed = True
                super().close()
        def connect(database_uri, **kwargs):
            self.assertEqual(database_uri,path.as_uri()+'?mode=ro')
            self.assertIs(kwargs.get('uri'),True)
            db = native_connect(database_uri,**kwargs,factory=ReadOnly)
            db.set_trace_callback(statements.append)
            connections.append(db)
            return db
        for schema in ('missing','tables','column','normal','corrupt'):
            with self.subTest(schema=schema):
                if path.exists():
                    path.unlink()
                if schema=='tables':
                    with contextlib.closing(native_connect(path)) as db:
                        db.execute('PRAGMA user_version=1')
                elif schema in ('column','normal'):
                    self.continuity_database(operator,missing_column=schema=='column')
                elif schema=='corrupt':
                    path.write_bytes(b'not a sqlite database')
                before = path.read_bytes() if path.exists() else None
                mode = path.stat().st_mode if path.exists() else None
                with patch('control_store.connect',side_effect=AssertionError('no writable store')), \
                        patch('control_store.root',side_effect=AssertionError('no creating root')), \
                        patch('control_store._bootstrap',side_effect=AssertionError('no migrations')), \
                        patch.object(Path,'chmod',side_effect=AssertionError('no chmod')), \
                        patch.object(os,'chmod',side_effect=AssertionError('no chmod')), \
                        patch.object(worker.sqlite3,'connect',side_effect=connect), \
                        patch.object(operator,'kubectl',side_effect=kubectl):
                    if schema=='corrupt':
                        with self.assertRaises(worker.sqlite3.DatabaseError):
                            operator.verify_secret_identities(self.operation)
                    else:
                        operator.verify_secret_identities(self.operation)
                        first = self.operation['secret_observations'][0]
                        initial = copy.deepcopy(first)
                        first.update(exists=False,uid=None,resource_version=None,immutable=None)
                        with self.assertRaises(RuntimeError):
                            operator.verify_secret_identities(self.operation)
                        first.update(initial)
                self.assertTrue(all(db.closed for db in connections))
                self.assertEqual(path.read_bytes() if path.exists() else None,before)
                self.assertEqual(path.stat().st_mode if path.exists() else None,mode)
        self.assertTrue(any(sql=='PRAGMA query_only=ON' for sql in statements))
        self.assertTrue(all(sql.startswith(('PRAGMA query_only=ON','SELECT ')) for sql in statements))

    def test_bootstrap_secret_identities_do_not_consult_full_continuity_fallback(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        operator.phase = 'bootstrap'
        del operator.receipt['secret_after']
        with patch.object(operator, '_reconciled_secret_after', side_effect=AssertionError('full only')) as fallback, \
                patch.object(operator, 'kubectl', side_effect=kubectl):
            operator.verify_secret_identities(self.operation)
            fallback.assert_not_called()

    def test_completed_verify_lost_core_ack_reconciles_under_gate_without_post(self):
        import warehouse_operations as core
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=http):
            completed = operator.verify(self.operation)
        saved = json.loads(worker.private_file(operator.receipt_path))
        self.assertEqual(saved['verification_complete']['evidence_ref'], completed['evidence_ref'])
        self.assertEqual(operator.receipt_path.stat().st_mode & 0o777, 0o600)
        current = dict(self.operation, revision=99, state='UNKNOWN',
            steps=[dict(step=step, state='UNKNOWN' if step == 'verify' else 'SUCCEEDED')
                   for step in worker.STEPS+('verify',)])
        args = argparse.Namespace(config=self.path, command='reconcile', operation_id='op', step='verify')
        principal = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        with patch.object(worker.Operator, 'principal', return_value=principal), \
                patch.object(core, 'inspect', return_value=current), \
                patch.object(core, '_reconcile_locked', return_value={'state': 'AWAITING_APPLY'}) as commit, \
                patch.object(worker.Operator, 'kubectl', side_effect=kubectl), \
                patch.object(worker.Operator, 'root_api', side_effect=lambda endpoint, realm, credentials: self.apis[realm]), \
                patch.object(worker.Operator, 'verify_runtime_handoff') as runtime, \
                patch.object(worker.Operator, 'http', side_effect=lambda request, **kwargs: http(request, **kwargs)
                    if '/oauth/tokens' in request.full_url else self.fail('SQL must not be resubmitted')) as transport, \
                patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref': 'snapshot:exact'}) as snapshot:
            self.assertEqual(worker.run(args), {'state': 'AWAITING_APPLY'})
            evidence = commit.call_args.kwargs['evidence']
            self.assertIs(type(evidence), core.ReconciliationEvidence)
            self.assertEqual((evidence.step, evidence.outcome, evidence.basis), ('verify', 'SUCCEEDED', 'observed_configuration'))
            self.assertEqual(evidence.evidence_ref, completed['evidence_ref'])
            snapshot.assert_called_once()
            self.assertEqual(transport.call_count, 2)
            runtime.assert_called_once_with(current)
        self.assertEqual(json.loads(worker.private_file(operator.receipt_path))['verification_complete'], saved['verification_complete'])

    def test_verify_reconciliation_missing_incomplete_or_changed_proof_remains_unknown(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=http):
            operator.verify(self.operation)
        original = copy.deepcopy(operator.receipt)
        ref = self.bundle['tofu']['bootstrap_secret_refs'][0]
        secret = objects[('secret', ref['namespace'], ref['name'])]
        original_uid = secret['metadata']['uid']
        for mismatch in ('missing_completion', 'incomplete_sql', 'changed_secret', 'changed_snapshot'):
            with self.subTest(mismatch=mismatch):
                operator.receipt = copy.deepcopy(original)
                secret['metadata']['uid'] = original_uid
                if mismatch == 'missing_completion':
                    operator.receipt.pop('verification_complete')
                elif mismatch == 'incomplete_sql':
                    next(iter(operator.receipt['sql'].values()))['complete'] = False
                elif mismatch == 'changed_secret':
                    secret['metadata']['uid'] = 'recreated-after-verification'
                operator.save()
                snapshot_args = {'side_effect': RuntimeError('mismatch')} if mismatch == 'changed_snapshot' else {
                    'return_value': {'evidence_ref': 'snapshot:exact'}}
                with patch.object(operator, 'kubectl', side_effect=kubectl), \
                        patch.object(operator, 'http', side_effect=AssertionError('must not POST')) as transport, \
                        patch.object(local_gitops, 'observe_public_generation', **snapshot_args):
                    evidence = operator.reconcile(self.operation, 'verify')
                    self.assertEqual(evidence.outcome, 'UNKNOWN')
                    transport.assert_not_called()
                if mismatch != 'missing_completion':
                    self.assertEqual(json.loads(worker.private_file(operator.receipt_path))['verification_complete'],
                                     original['verification_complete'])

    def test_actual_verifier_wrong_k8_generation_secret_uid_catalog_and_trino(self):
        for mismatch in ('generation', 'secret_uid', 'catalog', 'trino'):
            with self.subTest(mismatch=mismatch):
                # Fresh private operation avoids reusing prior verification statement evidence.
                operation = dict(self.operation, id='verify-'+mismatch)
                operator = worker.Operator(self.path)
                operator.bind(operation)
                operator.check_receipt(operation)
                objects, kubectl, http, requests = self.candidate_backend(operator)
                operation['secret_observations'] = self.operation['secret_observations']
                if mismatch == 'generation':
                    objects[('deployment', 'shirokuma-dev', 'trino-local')]['metadata']['annotations'][generation.ANNOTATION] = 'wrong'
                if mismatch == 'secret_uid':
                    ref = self.bundle['tofu']['bootstrap_secret_refs'][0]
                    objects[('secret', ref['namespace'], ref['name'])]['metadata']['uid'] = 'recreated'
                def mismatching_http(request, **kwargs):
                    value = http(request, **kwargs)
                    if mismatch == 'catalog' and '/api/management/' in request.full_url:
                        value['catalog']['name'] = 'wrong'
                    if mismatch == 'trino' and '/v1/statement' in request.full_url:
                        value['data'] = []
                    return value
                with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=mismatching_http), \
                        self.assertRaises(RuntimeError):
                    operator.verify(operation)

    def test_trino_verify_response_loss_never_posts_again(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        def lost_response(request, **kwargs):
            if '/v1/statement' in request.full_url:
                requests.append(request)
                raise RuntimeError('response lost')
            return http(request, **kwargs)
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=lost_response):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    operator.verify(self.operation)
        self.assertEqual(sum('/v1/statement' in r.full_url for r in requests), 1)

    def test_missing_ack_reconcile_positive_uploaded_files_and_children_only(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        operator.receipt['dispatches'] = ['publish_snapshot', 'reconcile_storage']
        operator.save()
        with patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref': 'snapshot:exact'}) as observe:
            self.assertEqual(operator.reconcile(self.operation, 'publish_snapshot').outcome, 'SUCCEEDED')
            observe.assert_called_once()
        with patch.object(operator, 'kubectl', side_effect=kubectl):
            self.assertEqual(operator.reconcile(self.operation, 'reconcile_storage').outcome, 'SUCCEEDED')
        with patch.object(operator, 'kubectl', side_effect=RuntimeError('notfound')):
            self.assertEqual(operator.reconcile(self.operation, 'reconcile_storage').outcome, 'UNKNOWN')
        operator.receipt['children_before']['shirokuma-dev']['uid'] = 'different'
        operator.save()
        with patch.object(operator, 'kubectl', side_effect=kubectl):
            self.assertEqual(operator.reconcile(self.operation, 'reconcile_storage').outcome, 'UNKNOWN')

    def test_missing_secret_ack_requires_bound_material_state_uid_and_suspension(self):
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator)
        for child in operator.children:
            objects[('kustomization', 'flux-system', child['metadata']['name'])]['spec']['suspend'] = True
        ref = self.bundle['tofu']['bootstrap_secret_refs'][0]
        live = objects[('secret', ref['namespace'], ref['name'])]
        after = dict(metadata=[dict(namespace=ref['namespace'], name=ref['name'], annotations=live['metadata']['annotations'])],
                     immutable=True, data={k: base64.b64decode(v).decode() for k, v in live['data'].items()})
        state = dict(lineage='fixture-lineage', serial=10, resources=[dict(mode='managed', type='kubernetes_secret_v1',
            instances=[dict(attributes=dict(after, metadata=[dict(after['metadata'][0], uid=live['metadata']['uid'])]))])])
        self.private('terraform-state', worker.canonical(state))
        operator.receipt['dispatches'] = ['apply_secrets']
        operator.receipt['tofu_dispatched'] = True
        operator.save()
        def saved_plan():
            operator.saved_plan = {'resource_changes': [dict(type='kubernetes_secret_v1', change={'after': after})]}
        with patch.object(operator, 'validate_saved_plan', side_effect=saved_plan), patch.object(operator, 'kubectl', side_effect=kubectl):
            self.assertEqual(operator.reconcile(self.operation, 'apply_secrets').outcome, 'SUCCEEDED')
            live['metadata']['uid'] = 'different-live-uid'
            self.assertEqual(operator.reconcile(self.operation, 'apply_secrets').outcome, 'UNKNOWN')
            live['metadata']['uid'] = state['resources'][0]['instances'][0]['attributes']['metadata'][0]['uid']
            objects[('kustomization', 'flux-system', operator.children[0]['metadata']['name'])]['spec']['suspend'] = False
            self.assertEqual(operator.reconcile(self.operation, 'apply_secrets').outcome, 'UNKNOWN')

    def test_snapshot_observation_sdk_get_only_exact_bytes_and_no_missing_inference(self):
        operator = self.bound()
        expected = {operator.prefix+name: content for name, content in self.bundle['files'].items() if name.startswith('deploy/')}
        client = Mock()
        client.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(expected[kw['Key']])}
        sdk = types.SimpleNamespace(client=Mock(return_value=client))
        config = Mock()
        with patch.dict(sys.modules, {'boto3': sdk, 'botocore.config': types.SimpleNamespace(Config=config)}):
            result = local_gitops.observe_public_generation(self.bundle, operator.s3_credentials())
            self.assertTrue(result['evidence_ref'].startswith('snapshot-observed:'))
            self.assertEqual(client.get_object.call_count, len(expected))
            client.put_object.assert_not_called()
            self.assertEqual(config.call_args.kwargs['retries'], {'max_attempts': 0})
            client.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(b'wrong-generation-content')}
            with self.assertRaises(RuntimeError):
                local_gitops.observe_public_generation(self.bundle, operator.s3_credentials())
            client.get_object.side_effect = RuntimeError('notfound')
            operator.receipt['dispatches'] = ['publish_snapshot']
            operator.save()
            self.assertEqual(operator.reconcile(self.operation, 'publish_snapshot').outcome, 'UNKNOWN')

    def test_child_control_refuses_missing_core_intent_and_does_not_retry_lost_response(self):
        operator = self.bound()
        operator.receipt['dispatches'] = ['apply_secrets']
        operator.save()
        name = operator.children[0]['metadata']['name']
        with patch('warehouse_operations.inspect', return_value={'steps': [{'step': 'apply_secrets', 'state': 'PENDING'}]}), \
                patch.object(operator, 'kubectl') as kubectl, self.assertRaises(RuntimeError):
            operator.set_child_suspended(name, True, plan=self.operation, step='apply_secrets')
        kubectl.assert_not_called()
        before = dict(metadata={'uid': 'uid', 'resourceVersion': '1'}, spec={'suspend': False, 'path': './old-source'})
        with patch.object(operator, 'get_child', return_value=before), \
                patch.object(operator, 'kubectl', side_effect=RuntimeError('response lost')) as kubectl:
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    operator.set_child_suspended(name, True, plan=self.operation, step='apply_secrets')
            self.assertEqual(kubectl.call_count, 1)

    def test_existing_child_suspend_resume_fixed_actions_and_uid(self):
        operator = self.bound()
        operator.receipt['dispatches'] = ['apply_secrets', 'reconcile_storage']
        operator.save()
        name = operator.children[0]['metadata']['name']
        for suspended in (True, False):
            before = dict(metadata={'uid': 'same', 'resourceVersion': '7'}, spec={'suspend': not suspended, 'path': './existing'})
            after = dict(metadata={'uid': 'same', 'resourceVersion': '8'}, spec={'suspend': suspended, 'path': './existing'})
            with patch.object(operator, 'get_child', side_effect=[before, after]), patch.object(operator, 'kubectl') as kubectl:
                operator.set_child_suspended(name, suspended, plan=self.operation,
                    step='apply_secrets' if suspended else 'reconcile_storage')
                self.assertEqual(kubectl.call_args.args[:7], ('-n', 'flux-system', 'patch', 'kustomization/'+name,
                    '--type=json', '--field-manager=shirokuma-local-gitops', '-p'))
                actions = json.loads(kubectl.call_args.args[7])
                self.assertEqual(actions, [
                    {'op': 'test', 'path': '/metadata/uid', 'value': 'same'},
                    {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '7'},
                    {'op': 'add', 'path': '/spec/suspend', 'value': suspended},
                ])
                self.assertEqual(operator.receipt['children_before'][name]['spec']['path'], './existing')
        with self.assertRaises(ValueError):
            operator.set_child_suspended('caller-resource', True, plan=self.operation, step='apply_secrets')

    def test_fixed_step_dispatch_and_no_readiness_claim(self):
        operator = self.bound()
        events = []
        with patch.object(operator, 'upload', side_effect=lambda: events.append('upload')), \
                patch.object(worker, '_apply_saved_plan', side_effect=lambda *a: events.append('tofu')), \
                patch.object(operator, 'validate_saved_plan'), \
                patch.object(operator, 'suspend_children', side_effect=lambda p: events.append('suspend')), \
                patch.object(operator, 'observe_secrets', return_value=[]), \
                patch.object(operator, 'switch_source', side_effect=lambda: events.append('source')), \
                patch.object(operator, 'observe_health'), \
                patch.object(operator, 'apply_children', side_effect=lambda children, **kwargs: events.append([d['metadata']['name'] for d in children])) as apply:
            for step in worker.STEPS:
                operator.execute_step(step, self.operation)
        self.assertEqual([call.kwargs for call in apply.call_args_list],
            [dict(plan=self.operation, step=step) for step in worker.STEPS[2:] for _ in operator.group(step)])
        self.assertEqual(events, ['upload', 'suspend', 'tofu', 'source',
            ['shirokuma-dev'], ['shirokuma-object-storage'], ['shirokuma-catalog-database'],
            ['shirokuma-catalog-bootstrap'], ['shirokuma-catalog'], ['shirokuma-iceberg-bootstrap'], ['shirokuma-trino']])
        with patch.object(operator, 'observe_source', side_effect=RuntimeError('unavailable')), self.assertRaises(RuntimeError):
            operator.verify(self.operation)
        with self.assertRaises(ValueError):
            operator.execute_step('arbitrary-shell', self.operation)

    def test_explicit_bootstrap_requires_no_consumer_credentials_and_never_starts_consumers(self):
        self.config['phase'] = 'bootstrap'
        self.private('config', worker.canonical(self.config))
        self.private('candidate', worker.canonical(dict(version=1,
            generation_id=self.bundle['manifest']['generation_id'], databases={key: dict(polaris={
                'endpoint_url': entry['polaris']['endpoint_url']}) for key, entry in self.candidate['databases'].items()})))
        (self.root/'ca.pem').unlink()
        self.operation['phase'] = 'bootstrap'
        operator = self.bound()
        events = []
        def apply(children, **kwargs):
            for child in children:
                events.append(child['metadata']['name'])
                for health in child['spec'].get('healthChecks', []):
                    operator.observe_health(health)
        with patch.object(operator, 'upload'), patch.object(worker, '_apply_saved_plan'), \
                patch.object(operator, 'validate_saved_plan'), patch.object(operator, 'suspend_children'), \
                patch.object(operator, 'observe_secrets', return_value=[]), patch.object(operator, 'switch_source'), \
                patch.object(operator, 'apply_children', side_effect=apply) as apply, \
                patch.object(operator, 'observe_health', side_effect=lambda h: events.append('health:'+h['name'])), \
                patch.object(operator, 'provision_databases', side_effect=lambda p: events.append('provision')):
            for step in worker.STEPS[:-1]:
                operator.execute_step(step, self.operation)
        self.assertEqual([call.kwargs for call in apply.call_args_list],
            [dict(plan=self.operation, step=step) for step in worker.STEPS[2:-1] for _ in operator.group(step)])
        self.assertNotIn('shirokuma-trino', events)
        self.assertNotIn('shirokuma-iceberg-bootstrap', events)
        self.assertLess(events.index('health:seaweedfs'), events.index('shirokuma-catalog-bootstrap'))
        self.assertLess(events.index('shirokuma-catalog'), events.index('provision'))

    def test_runtime_handoff_compares_actual_private_credentials_and_live_profile(self):
        import runtime_profile
        import types
        operator = self.bound()
        objects, kubectl, http, requests = self.candidate_backend(operator, actual_runtime=True)
        operator.config.update(runtime_profile=str(self.root/'runtime.json'), runtime_endpoint='http://127.0.0.1:8888')
        profile = types.SimpleNamespace(state_dir=str(self.root/'state'), trino_auth_file='backend',
            catalog_auth_file='catalog', trino_ca_file=str(self.root/'ca.pem'), sql_endpoint_url='http://127.0.0.1:8080')
        backend = dict(platform_admin=dict(username='admin', password='admin-password'), databases={})
        catalog = dict(databases={})
        for key, entry in operator.bundle['tofu']['databases'].items():
            backend['databases'][key] = dict(roles=dict(data_engineer=self.candidate['databases'][key]['trino'],
                bi_viewer=dict(username='viewer-'+key, password='viewer-password')))
            catalog['databases'][key] = {k: base64.b64decode(v).decode() for k, v in
                operator.secret_objects[(entry['trino_secret']['namespace'], entry['trino_secret']['name'])]['data'].items()}
        identity = dict(generation_id='generation-one')
        with patch.object(runtime_profile, 'validate_generation', return_value=identity), \
                patch.object(runtime_profile, 'load', return_value=profile), \
                patch.object(runtime_profile, 'private_binding', return_value=operator.binding['runtime_profile']), \
                patch.object(runtime_profile, 'private_json', side_effect=lambda path: backend if path == 'backend' else catalog), \
                patch('database_material._verify_passwords') as passwords, \
                patch.object(operator, 'http', return_value=identity) as status:
            operator.verify_runtime_handoff(self.operation)
            passwords.assert_called_once()
            self.assertEqual([c.args[0].full_url for c in status.call_args_list],
                ['http://127.0.0.1:8888/api/v1/runtime', 'http://127.0.0.1:8080/api/v1/runtime'])
            status.side_effect = [identity, RuntimeError('native unavailable')]
            with self.assertRaises(RuntimeError):
                operator.verify_runtime_handoff(self.operation)
            status.side_effect = [identity, dict(generation_id='old')]
            with self.assertRaises(RuntimeError):
                operator.verify_runtime_handoff(self.operation)
            status.side_effect = None
            status.return_value = dict(generation_id='old-generation')
            with self.assertRaises(RuntimeError):
                operator.verify_runtime_handoff(self.operation)
            status.return_value = identity
            catalog['databases'][next(iter(catalog['databases']))]['client_secret'] = 'wrong'
            with self.assertRaises(RuntimeError):
                operator.verify_runtime_handoff(self.operation)

    def test_profile_and_all_referenced_bytes_are_bound_before_apply(self):
        operator = self.bound()
        for name in ('runtime.json', 'runtime-backend', 'runtime-catalog', 'runtime-accounts', 'runtime-client', 'ca.pem'):
            path = self.root/name
            before = path.read_bytes()
            path.write_bytes(before+b' ')
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    operator.check_receipt(self.operation)
            finally:
                path.write_bytes(before)

    def test_full_without_predecessor_requires_runtime_for_verification_and_reconciliation(self):
        operator = self.bound()
        self.assertIsNone(operator.predecessor_id)
        objects, kubectl, http, requests = self.candidate_backend(operator)
        with patch.object(operator, 'kubectl', side_effect=kubectl), patch.object(operator, 'http', side_effect=http):
            operator.verify_runtime_handoff.side_effect = RuntimeError('stale active runtime')
            with self.assertRaises(RuntimeError):
                operator.verify(self.operation)
            self.assertFalse(any('/v1/statement' in r.full_url for r in requests))
            operator.verify_runtime_handoff.side_effect = None
            operator.verify(self.operation)
            operator.verify_runtime_handoff.side_effect = RuntimeError('stale active runtime')
            with patch.object(local_gitops, 'observe_public_generation', return_value={'evidence_ref': 'snapshot:exact'}):
                evidence = operator.reconcile(self.operation, 'verify')
            self.assertEqual(evidence.outcome, 'UNKNOWN')

    def test_secret_metadata_and_version_drift(self):
        operator = self.bound()
        refs = self.bundle['tofu']['bootstrap_secret_refs'] + self.bundle['tofu']['deferred_secret_refs']
        def response(*args, **kwargs):
            ref = next(ref for ref in refs if ref['namespace'] == args[1] and ref['name'] == args[4])
            return json.dumps(dict(metadata=dict(name=ref['name'], namespace=ref['namespace'], uid='uid',
                resourceVersion='12', annotations={worker.VERSION_ANNOTATION: ref['version']}), immutable=True,
                data={key: 'secret-test-value' for key in ref['keys']}))
        with patch.object(operator, 'kubectl', side_effect=response):
            observed = operator.observe_secrets(None)
        self.assertEqual(len(observed), len(refs))
        self.assertNotIn('secret-test-value', json.dumps(observed))
        self.assertTrue(all(row['uid'] == 'uid' and row['resource_version'] == '12' for row in observed))
        invalid = json.loads(response('-n', refs[0]['namespace'], 'get', 'secret', refs[0]['name']))
        invalid['metadata']['annotations'][worker.VERSION_ANNOTATION] = 'changed'
        with patch.object(operator, 'kubectl', return_value=json.dumps(invalid)), self.assertRaises(ValueError):
            operator.observe_secrets(None)
        with patch.object(operator, 'kubectl', side_effect=RuntimeError('unavailable')), self.assertRaises(RuntimeError):
            operator.observe_secrets(None)
        with patch.object(operator, 'kubectl', return_value=''):
            self.assertTrue(all(row['exists'] is False and row['uid'] is None and row['immutable'] is None
                                for row in operator.observe_secrets(None)))

    def test_exact_child_observation_rejects_stale_generation_and_spec(self):
        operator = self.bound()
        desired = operator.children[0]
        actual = copy.deepcopy(desired)
        actual['metadata'].update(uid='uid', generation=2)
        actual['status'] = {'conditions': [{'type': 'Ready', 'status': 'True', 'observedGeneration': 1}]}
        with patch.object(operator, 'get_child', return_value=actual), self.assertRaises(RuntimeError):
            operator.observe_child(desired)
        actual['status']['conditions'][0]['observedGeneration'] = 2
        actual['spec']['path'] = './wrong'
        with patch.object(operator, 'get_child', return_value=actual), self.assertRaises(RuntimeError):
            operator.observe_child(desired)

    def test_sdk_upload_only_explicit_credentials_and_zero_retries(self):
        client = Mock()
        boto = types.SimpleNamespace(client=Mock(return_value=client))
        config = Mock(return_value='sdk-config')
        with patch.dict(sys.modules, {'boto3': boto, 'botocore.config': types.SimpleNamespace(Config=config)}), \
                patch.object(local_gitops, 'upload_files') as upload, \
                patch.object(local_gitops, 'kubectl') as kubectl, \
                patch.object(local_gitops, 'operator_credentials') as fallback:
            operator = self.bound()
            operator.upload()
            self.assertEqual(config.call_args.kwargs['retries'], {'max_attempts': 0})
            self.assertEqual(config.call_args.kwargs['s3'], {'addressing_style': 'path'})
            self.assertEqual(boto.client.call_args.kwargs['aws_access_key_id'], 'operator')
            upload.assert_called_once()
            client.close.assert_called_once()
            kubectl.assert_not_called()
            fallback.assert_not_called()

    def test_real_core_plan_apply_order_failure_closed_and_current_auth_revocation(self):
        self.intent_patch.stop()
        import control_store
        import data_management as dm
        import warehouse_operations as core
        spec_value = self.bundle['manifest']['spec']
        salt = '11'*16
        accounts = self.private('accounts', worker.canonical({'admin': dict(role='admin', platform_admin=True,
            salt=salt, password_hash=request_auth.password_hash('test-password', salt))}))
        token = base64.b64encode(b'admin:test-password').decode()
        self.private('auth', worker.canonical({'Authorization': 'Basic '+token, 'X-Shirokuma-Role': 'admin'}))
        refs = self.bundle['tofu']['bootstrap_secret_refs'] + self.bundle['tofu']['deferred_secret_refs']
        observations = [dict(ref, exists=False, uid=None, resource_version=None, immutable=None)
                        for ref in refs]
        args = argparse.Namespace(config=self.path, command='plan', idempotency_key='integration')
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root/'state'),
                        SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(accounts)):
            with control_store.connect() as db:
                for key, value in spec_value['databases'].items():
                    db.execute('INSERT INTO databases VALUES (?,?,?,?,?,?,?)',
                               (key, value['sql_catalog'], key, '', 'user', 'READY', 1))
                    dm.create_binding(db, key, **{k: v for k, v in value['binding'].items() if k != 'desired_revision'})
                    for schema in value['schemas']:
                        db.execute('INSERT INTO schemas VALUES (?,?,?,?,?,?,?)',
                            (schema['id'], key, schema['sql_name'], schema['sql_name'], schema['purpose'], 'user', schema['revision']))
            with patch.object(worker.Operator, 'validate_saved_plan'), \
                    patch.object(worker.Operator, 'observe_secrets', return_value=observations):
                plan = worker.run(args)
                args.command, args.operation_id, args.expected_revision = 'apply', plan['id'], plan['revision']
                events = []
                def applied_secrets(*args, **kwargs):
                    events.append('apply_secrets')
                    observations[:] = [dict(ref, exists=True, uid='uid-'+ref['name'], resource_version='1', immutable=True)
                                       for ref in refs]
                with patch.object(worker.Operator, 'upload', side_effect=lambda: events.append('publish_snapshot')), \
                        patch.object(worker, '_apply_saved_plan', side_effect=applied_secrets), \
                        patch.object(worker.Operator, 'switch_source'), \
                        patch.object(worker.Operator, 'suspend_children'), \
                        patch.object(worker.Operator, 'verify', side_effect=RuntimeError('candidate mismatch')), \
                        patch.object(worker.Operator, 'observe_health'), \
                        patch.object(worker.Operator, 'apply_children', side_effect=lambda children, **kwargs: events.extend(
                            ['reconcile_storage'] if children[0]['metadata']['name'] == 'shirokuma-dev' else
                            ['reconcile_polaris'] if children[0]['metadata']['name'] == 'shirokuma-catalog-bootstrap' else
                            ['reconcile_trino'] if children[0]['metadata']['name'] == 'shirokuma-trino' else [])):
                    result = worker.run(args)
                self.assertEqual(events, list(worker.STEPS))
                self.assertEqual(result['state'], 'UNKNOWN')
                self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
                self.assertTrue(all(row['state'] == 'SUCCEEDED' for row in result['steps'][:-1]))
                with patch.object(worker.Operator, 'execute_step') as execute, self.assertRaises(core.OperationError):
                    worker.run(args)
                execute.assert_not_called()
                data = json.loads(accounts.read_bytes())
                data['admin']['platform_admin'] = False
                accounts.write_bytes(worker.canonical(data))
                with patch.object(worker.Operator, 'execute_step') as execute, self.assertRaises(PermissionError):
                    worker.run(args)
                execute.assert_not_called()


from tests.test_database_creations import CreationFixture


class OperatorLeafGateTests(CreationFixture):
    """Real approvals and STOP transactions fence sends after earlier observations."""
    def operator(self, step):
        import control_store
        import database_creation_auth as delegation
        import warehouse_operations as core
        ack = self.create()
        approval, linked, observations = self.approve(ack)
        permit = delegation.claim(self.config, self.executor, approval['request_id'], owner_id='owner')
        plan = core.inspect(linked['operation_id'], actor='alice')
        with delegation.delegated(permit), delegation.transaction() as db:
            db.execute("UPDATE warehouse_controls SET state='APPLYING',operation_id=?,revision=revision+1 WHERE warehouse_id=?",
                       (plan['id'], plan['warehouse_id']))
            db.execute('UPDATE operation_requests SET attempt_id=?,mutation_started=1 WHERE operation_id=?',
                       (permit.claim_id, plan['id']))
            db.execute("UPDATE operation_steps SET state='RUNNING' WHERE operation_id=? AND step=?", (plan['id'], step))
            core._state(db, plan['id'], 'APPLYING')
        plan = core.inspect(linked['operation_id'], actor='alice')
        operator = worker.Operator.__new__(worker.Operator)
        private = self.directory/'private'
        private.mkdir(mode=0o700)
        operator.receipt_path = private/(plan['id']+'.json')
        operator.receipt = dict(actor='alice', dispatches=[step], acknowledgements={},
            children_before={}, child_dispatches=[], sql={})
        operator.bundle = generation.render(plan['manifest']['spec'])
        operator.binding = {'generation_id': plan['generation_id']}
        operator.children = operator.bundle['children'] if 'children' in operator.bundle else []
        operator.prefix = local_gitops.generation_prefix(plan['delivery_digest'])
        operator.config = dict(tofu_plan='/sealed-plan', tofu_state='/sealed-state', tofu_workdir='/sealed-workdir')
        operator.save()
        operator._active_intent = (plan, step)
        return operator, plan, permit

    def stop(self):
        import database_creation_auth as delegation
        return delegation.stop_principal(self.config, self.executor, 'alice', 'stop-between-observation-and-send')

    def test_stop_after_child_observation_prevents_patch_and_retains_intent(self):
        import database_creation_auth as delegation
        import warehouse_operations as core
        operator, plan, permit = self.operator('apply_secrets')
        operator.children = [dict(metadata=dict(name='child'))]
        def observe(desired):
            self.stop()
            return dict(metadata=dict(uid='uid', resourceVersion='1'), spec=dict(suspend=False))
        with delegation.delegated(permit), patch.object(operator, 'get_child', side_effect=observe), \
                patch.object(operator, 'kubectl') as send, self.assertRaises(core.OperationError):
            operator.set_child_suspended('child', True, plan=plan, step='apply_secrets')
        send.assert_not_called()
        persisted = json.loads(operator.receipt_path.read_bytes())
        self.assertEqual(persisted['child_dispatches'], ['child:True'])
        self.assertEqual(len(persisted['leaf_intents']), 1)

    def test_stop_during_final_state_observation_prevents_tofu_send(self):
        import database_creation_auth as delegation
        import warehouse_operations as core
        operator, plan, permit = self.operator('apply_secrets')
        operator.receipt['dispatches'] = []
        checks = []
        def observe():
            checks.append('check')
            if len(checks) == 2:
                self.stop()
        with delegation.delegated(permit), patch.object(operator, 'check_receipt'), \
                patch.object(operator, 'check_state_before_apply', side_effect=observe), \
                patch.object(operator, 'validate_saved_plan'), patch.object(operator, 'suspend_children'), \
                patch.object(worker, '_apply_saved_plan') as send, self.assertRaises(core.OperationError):
            operator.execute_step('apply_secrets', plan)
        send.assert_not_called()
        self.assertTrue(json.loads(operator.receipt_path.read_bytes())['tofu_dispatched'])

    def test_stop_after_source_get_prevents_source_apply(self):
        import database_creation_auth as delegation
        import warehouse_operations as core
        operator, plan, permit = self.operator('reconcile_storage')
        source = dict(apiVersion='v1', kind='Bucket', metadata=dict(name='source', namespace='flux-system', uid='kept'), spec={})
        calls = []
        def observe(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return json.dumps(dict(spec=dict(suspend=True)))
            self.stop()
            return json.dumps(source)
        with delegation.delegated(permit), patch.object(operator, 'kubectl', side_effect=observe), \
                self.assertRaises(core.OperationError):
            operator.switch_source()
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(call[0] == 'apply' for call in calls))
        self.assertEqual(json.loads(operator.receipt_path.read_bytes())['source_uid'], 'kept')

    def test_stop_after_sql_dispatch_journal_prevents_post(self):
        import database_creation_auth as delegation
        import warehouse_operations as core
        operator, plan, permit = self.operator('verify')
        key = next(iter(operator.bundle['manifest']['spec']['databases']))
        save = operator.save
        stopped = []
        def journal():
            save()
            if key in operator.receipt['sql'] and not stopped:
                stopped.append(True)
                self.stop()
        with delegation.delegated(permit), patch.object(operator, 'save', side_effect=journal), \
                patch.object(operator, 'http') as send, self.assertRaises(core.OperationError):
            operator.verify_trino(key, dict(username='scoped-user', password='private-password'),
                dict(endpoint_url='https://127.0.0.1:8443', ca_file='/private-ca'), plan)
        send.assert_not_called()
        self.assertTrue(json.loads(operator.receipt_path.read_bytes())['sql'][key]['dispatched'])

    def test_provider_mutation_and_token_post_both_require_current_gate(self):
        import database_creation_auth as delegation
        import warehouse_operations as core
        operator, plan, permit = self.operator('reconcile_polaris')
        opener = Mock()
        credentials = dict(client_id='root', client_secret='private')
        with delegation.delegated(permit), patch.object(operator, 'http', return_value=dict(access_token='private-token')) as token, \
                patch.object(worker.urllib.request, 'build_opener', return_value=opener):
            request = operator.root_api('http://127.0.0.1:8181', 'realm', credentials)
            self.assertEqual(token.call_count, 1)
            self.stop()
            with self.assertRaises(core.OperationError):
                request('POST', '/api/management/v1/principals', dict(name='new-principal'))
            with self.assertRaises(core.OperationError):
                operator.consumer_token('http://127.0.0.1:8181', 'realm', credentials)
            self.assertEqual(token.call_count, 1)
        opener.open.assert_not_called()

    def test_stop_during_first_snapshot_put_keeps_ack_and_prevents_next_put(self):
        import database_creation_auth as delegation
        import warehouse_operations as core
        operator, plan, permit = self.operator('publish_snapshot')
        client = Mock()
        def put(**values):
            self.stop()
            return dict(ETag='known-ack')
        client.put_object.side_effect = put
        sdk = types.SimpleNamespace(client=Mock(return_value=client))
        class ClientError(Exception):
            pass
        modules = {'boto3': sdk, 'botocore.config': types.SimpleNamespace(Config=Mock()),
                   'botocore.exceptions': types.SimpleNamespace(ClientError=ClientError)}
        credentials = dict(endpoint_url='http://127.0.0.1:8333', region='local', access_key_id='key', secret_access_key='secret')
        with delegation.delegated(permit), patch.dict(sys.modules, modules), \
                patch.object(operator, 's3_credentials', return_value=credentials), self.assertRaises(core.OperationError):
            operator.upload()
        self.assertEqual(client.put_object.call_count, 1)
        client.close.assert_called_once()
        persisted = json.loads(operator.receipt_path.read_bytes())
        self.assertEqual(list(persisted['snapshot_puts'].values()), [dict(ETag='known-ack')])
        self.assertEqual(len(persisted['leaf_intents']), 2)


class SavedPlanLeafTests(unittest.TestCase):
    def test_exact_fixed_argv_without_operator_or_artifact_reads(self):
        with patch.object(worker, 'Operator', side_effect=AssertionError('must not instantiate')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read artifacts')), \
                patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='private')) as run:
            self.assertIsNone(worker._apply_saved_plan('/explicit/saved-plan', '/explicit/state', '/public/module'))
        run.assert_called_once_with(['rtk', 'proxy', 'tofu', 'apply', '-input=false', '-no-color',
            '-state=/explicit/state', '/explicit/saved-plan'], cwd='/public/module', capture_output=True,
            text=True, timeout=180, umask=0o077)

    def test_failure_and_response_loss_are_sanitized_and_never_retried(self):
        for response in (types.SimpleNamespace(returncode=1, stdout='private-secret', stderr='private-secret'),
                         subprocess.TimeoutExpired('private-secret', 180), OSError('private-secret')):
            with self.subTest(response=type(response).__name__):
                arguments = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                with patch.object(worker.subprocess, 'run', **arguments) as run, self.assertRaises(RuntimeError) as error:
                    worker._apply_saved_plan('/plan', '/state', '/module')
                self.assertNotIn('private-secret', str(error.exception))
                self.assertIsNone(error.exception.__cause__)
                self.assertEqual(run.call_count, 1)
