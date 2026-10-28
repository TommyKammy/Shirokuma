"""P3-1 boundary contracts, with durable SQL intents and a real HTTP failure."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import dataops_service as ds
import database_inventory as inv
import query_service as qs
from query_context import QueryError, resolve_context
import request_auth
import warehouse_admission as admission


class DataOpsTests(unittest.TestCase):

    def retire(self, state='retiring', database=None):
        with control_store.connect() as db:
            db.execute('UPDATE databases SET state=?,revision=revision+1 WHERE id=?',
                       (state, database or self.databases[0]))

    def assert_database_admissions(self, database=None):
        with control_store.connect() as db:
            rows = list(db.execute('SELECT database_id FROM warehouse_admissions'))
        self.assertTrue(rows)
        self.assertEqual({r[0] for r in rows}, {database or self.databases[0]})

    def _running_poll_operation(self):
        execution = self.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        methods = []
        def transport(url, method, pin, body=None):
            self.assertEqual(pin.headers['Authorization'], 'Basic original-secret')
            methods.append(method)
            if method == 'DELETE':
                return {}
            return dict(id='operation_poll', data=[[1]],
                nextUri='http://127.0.0.1:1234/v1/statement/operation_poll/1')
        self.start_patch('query_service._transport', side_effect=transport)
        page = qs._submit_dataops(self.principal, ds._Statement('select', 'SELECT 1'),
                                  frozen, parent, execution.id, 'poll')
        return execution, page, methods

    def _assert_retiring_inspect_denied(self, execution):
        with self.assertRaises(QueryError) as denied:
            execution.inspect()
        self.assertEqual((denied.exception.status, denied.exception.details), (403, {}))

    def test_retiring_inspect_preserves_query_until_grant_loss_cleanup(self):
        execution, page, methods = self._running_poll_operation()
        self.retire()
        self._assert_retiring_inspect_denied(execution)
        self.assertEqual(methods, ['POST'])
        self.assertEqual(qs._row(page['id'])['state'], 'RUNNING')
        with control_store.connect() as db:
            db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (self.databases[0],))
        self._assert_retiring_inspect_denied(execution)
        self.assertEqual(methods, ['POST', 'DELETE'])
        self.assertEqual(qs._row(page['id'])['state'], 'CANCELED')

    def test_retiring_inspect_preserves_query_until_account_loss_cleanup(self):
        execution, page, methods = self._running_poll_operation()
        self.retire()
        self._assert_retiring_inspect_denied(execution)
        self.assertEqual(methods, ['POST'])
        del self.fixture.users['alice']
        self.fixture.save()
        self._assert_retiring_inspect_denied(execution)
        self.assertEqual(methods, ['POST', 'DELETE'])
        self.assertEqual(qs._row(page['id'])['state'], 'CANCELED')

    def test_retiring_unknown_inspect_keeps_reconciliation_guard_and_query_pin(self):
        execution, page, methods = self._running_poll_operation()
        token = page['next_uri'].rsplit('/', 1)[1]
        frozen_role = self.context().selected_role
        with patch.object(qs, '_transport', side_effect=OSError('lost page')):
            with self.assertRaises(QueryError):
                qs.fetch(self.principal, page['id'], token, selected_role=frozen_role)
        self.retire()
        self._assert_retiring_inspect_denied(execution)
        self.assertEqual(methods, ['POST'])
        self.assertEqual(execution._row()['state'], 'UNKNOWN')
        self.assertEqual(qs._row(page['id'])['state'], 'UNKNOWN')
        with control_store.connect() as db:
            self.assertEqual({r['state'] for r in admission.blockers(db, 'w')}, {'UNKNOWN'})
        self.assertEqual(qs.cancel(self.principal, page['id'], selected_role=frozen_role)['state'], 'CANCELED')
        self.assertEqual(methods, ['POST', 'DELETE'])
        self.assertEqual(execution._row()['state'], 'UNKNOWN')

    def test_retiring_private_mutators_continue_but_public_observation_rejects(self):
        execution = self.begin()
        ref = self.existing()
        self.retire()
        with self.assertRaises((QueryError, PermissionError)):
            execution.inspect()
        execution.insert_rows(ref, ['id'], [(17,)], step='write')
        execution.select(ref, ['id'], step='read')
        self.assertEqual(execution.complete()['state'], 'FINISHED')
        self.assert_database_admissions()
        with self.assertRaises((QueryError, PermissionError)):
            execution.inspect()

    def test_public_inspect_is_strict_inside_private_scope_and_restores_parent(self):
        execution = self.begin()
        ref = self.existing()
        self.retire()
        parent = ds._live[execution.id][1]
        with ds._accepted(execution):
            with self.assertRaises(QueryError):
                execution.inspect()
            self.assertIs(admission._current.get(), parent)
            execution.select(ref, ['id'], step='nested-child')
            self.assertIs(admission._current.get(), parent)
            self.assertEqual(execution._inspect()['state'], 'ACTIVE')
        self.assertIsNone(admission._current.get())

    def test_private_inspect_terminal_race_does_not_enter_public_inspect(self):
        execution = self.begin()
        execution.fail()
        ds._live.pop(execution.id)
        self.retire()
        row = execution._row()
        # The initial read raced with a worker's definite terminal settlement.
        with patch.object(execution, '_row', side_effect=[dict(row, state='ACTIVE'), row]), \
                patch.object(execution, 'inspect', side_effect=AssertionError('public recursion')):
            self.assertEqual(execution._inspect()['state'], 'FAILED')

    def test_retiring_does_not_override_revoked_grant(self):
        execution = self.begin()
        ref = self.existing()
        self.retire()
        with control_store.connect() as db:
            db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?",
                       (self.databases[0],))
        with self.assertRaises((QueryError, PermissionError)):
            execution.insert_rows(ref, ['id'], [(17,)], step='denied')
        self.assertFalse(self.calls)

    def test_retiring_does_not_override_removed_account_or_allow_cancel(self):
        execution = self.begin()
        self.retire()
        del self.fixture.users['alice']
        self.fixture.save()
        with self.assertRaises((QueryError, PermissionError)):
            execution.create_table(self.locator(), [('id', 'BIGINT')], step='denied')
        with self.assertRaises((QueryError, PermissionError)):
            execution.cancel()
        self.assertFalse(self.calls)

    def test_retired_parent_and_exact_database_mismatch_cannot_continue(self):
        execution = self.begin()
        parent = ds._live[execution.id][1]
        with admission.accepted(parent), control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises((admission.AdmissionDenied, admission.AdmissionConflict)):
                admission.admit(db, warehouse_id='w', actor=self.principal.principal_id,
                    kind='query', subject_id='wrong-db', parent=parent,
                    generation_id=parent.generation_id, database_id=self.databases[1])
        self.retire('retired')
        with self.assertRaises((QueryError, PermissionError, admission.AdmissionDenied, admission.AdmissionConflict)):
            execution.create_table(self.locator(), [('id', 'BIGINT')], step='denied')
        self.assertFalse(self.calls)

    def setUp(self):
        from test_request_auth import PrincipalTests
        self.fixture = PrincipalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.clear_pins)
        self.clear_pins()
        self.principal = self.fixture.principal()
        self.databases = [self.fixture.sales, self.fixture.finance]
        with control_store.connect() as db:
            third = dm.create_database(db, 'research', 'Research', '', 'test').id
            dm.create_binding(db, third, 'service', third, 'catalog', 'storage', 'credentials', 's3://bucket')
            dm.record_generation(db, dm.WarehouseConfigGeneration('g-' + third, 'w', third, (), (), (), 'READY'))
            dm.update_binding(db, third, 1, 1, 'g-' + third)
            db.execute("UPDATE databases SET state='READY' WHERE id=?", (third,))
            self.databases.append(third)
            self.schemas = {d: {n: dm.create_schema(db, d, n, n, n, 'test').id
                                   for n in ('raw', 'sandbox', 'marts')} for d in self.databases}
            for database in self.databases[1:]:
                dm.create_grant(db, 'alice', database, 'data_engineer')
            from grant_fixtures import verified_grants
            verified_grants(db)
        self.objects, self.calls = {}, []
        self.credential = 'Basic original-secret'
        self.start_patch('trino_auth.prepare_backend_v2', side_effect=self.prepare)
        self.start_patch('trino_auth.backend_ssl_context', return_value=None)
        self.start_patch('trino_auth.validate_backend_url')
        self.start_patch('warehouse_resident.persistent_trino', self.backend)
        self.start_patch('warehouse_resident.warehouse_activity', self.activity)
        self.start_patch('database_inventory.catalog_api', self.catalog)
        self.transport_patch = patch.object(qs, '_transport', side_effect=self.transport)
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)

    def clear_pins(self):
        for pin in qs._pins.values():
            qs._close_query_owner(pin)
        for _, _, owner, _ in ds._live.values():
            owner.close()
        qs._pins.clear()
        ds._live.clear()
        qs._origin = None

    def start_patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    @contextmanager
    def backend(self):
        yield 'http://127.0.0.1:1234'

    @contextmanager
    def activity(self):
        yield

    def prepare(self, *, principal, database_id, selected_role):
        decision = request_auth.authorize_database(principal, database_id, selected_role,
                                                  expected_generation_id='g-' + database_id)
        return decision, {'X-Trino-User': 'fixed_' + database_id, 'Authorization': self.credential}

    @contextmanager
    def catalog(self, database, binding):
        def api(path):
            match = re.fullmatch(r'/namespaces/([^/]+)/(tables|views)/([^/]+)', path)
            if not match:
                raise AssertionError(path)
            schema, kind, name = match.groups()
            key = (database.sql_catalog, schema, name, kind)
            if key not in self.objects:
                raise inv.InventoryError('NOT_FOUND', 404, 'missing')
            return {'metadata': self.objects[key]}
        yield api

    def transport(self, url, method, pin, body=None):
        sql = body.decode() if body is not None else None
        self.calls.append((method, sql, dict(pin.headers)))
        if method == 'POST':
            with control_store.connect() as db:
                row = db.execute('''SELECT d.*,o.context,o.actor FROM dataops_queries d
                    JOIN query_execution_contexts e ON e.query_id=d.query_id
                    JOIN dataops_operations o ON o.id=d.operation_id WHERE e.sql=?''', (sql,)).fetchone()
                self.assertIsNotNone(row, 'child relation and parent must precede POST')
                self.assertTrue(json.loads(row['context'])['database_id'])
            if sql.startswith('CREATE TABLE '):
                names = re.findall(r'"([^"]+)"', sql)[:3]
                self.objects[(*names, 'tables')] = {'table-uuid': str(uuid.uuid4())}
            if sql.startswith('DROP TABLE '):
                names = re.findall(r'"([^"]+)"', sql)[:3]
                self.objects.pop((*names, 'tables'))
        return {'id': 'q_' + str(len(self.calls)), 'data': [[7], [19], [23]] if sql and sql.startswith('SELECT') else []}

    def context(self, database=None, schema='raw', principal=None, role='data_engineer'):
        database = database or self.databases[0]
        return resolve_context(principal or self.principal, database, self.schemas[database][schema], role,
                               warehouse_id='w', expected_binding_revision=1)

    def begin(self, key='request', **kwargs):
        return ds.begin(kwargs.pop('principal', self.principal), kwargs.pop('context', self.context()),
                        route='manual-ingest', idempotency_key=key, payload=kwargs.pop('payload', {'mode': 'test'}), **kwargs)

    def locator(self, name='arbitrary_data', database=None, schema='raw'):
        database = database or self.databases[0]
        return dm.DestinationLocator(database, self.schemas[database][schema], name)

    def existing(self, name='observed_data', database=None, schema='raw', kind='TABLE'):
        locator = self.locator(name, database, schema)
        with control_store.connect() as db:
            catalog = dm.get_database(db, locator.database_id).sql_catalog
            identity = str(uuid.uuid4())
            obj = dm.observe_object(db, locator.schema_id, name, kind, identity, name)
        self.objects[(catalog, schema, name, 'tables' if kind == 'TABLE' else 'views')] = {
            'table-uuid' if kind == 'TABLE' else 'view-uuid': identity}
        return dm.ResourceRef(locator.database_id, locator.schema_id, obj.id, obj.incarnation)

    def test_three_database_arbitrary_names_and_values(self):
        for index, database in enumerate(self.databases):
            execution = self.begin('db-' + str(index), context=self.context(database))
            ref = execution.create_table(self.locator('shared_name', database), [('seq', 'BIGINT'), ('label', 'VARCHAR')], step='create')
            execution.insert_rows(ref, ['seq', 'label'], [(11 + index, '001'), (31, ''), (43, None), (59, "a'b")], step='insert')
            result = execution.select(ref, ['seq'], step='read')
            self.assertEqual(result['data'], [[7], [19], [23]])
            self.assertEqual(execution.complete(committed=True)['state'], 'FINISHED')
        sql = [call[1] for call in self.calls]
        self.assertTrue(any('"research"."raw"."shared_name"' in statement for statement in sql))
        self.assertTrue(any("'a''b'" in statement and "'001'" in statement for statement in sql))

    def test_create_conflicts_with_table_and_uuidless_view(self):
        execution = self.begin()
        original_catalog = self.catalog
        @contextmanager
        def normalized_catalog(database, binding):
            try:
                with original_catalog(database, binding) as api:
                    yield api
            except inv.InventoryError:
                raise
            except Exception:
                raise inv.InventoryError('CATALOG_UNAVAILABLE', 502, 'catalog body normalization')
        catalog_patch = patch.object(inv, 'catalog_api', normalized_catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        for kind in ('tables', 'views'):
            self.objects[('sales', 'raw', 'conflict_name', kind)] = {}
            with self.assertRaises(QueryError) as caught:
                execution.create_table(self.locator('conflict_name'), [('id', 'INTEGER')], step='create')
            self.assertEqual(caught.exception.status, 409)
            self.objects.clear()
        self.assertFalse(self.calls)

    def test_no_ref_before_successful_create_and_observation(self):
        execution = self.begin()
        with patch.object(self, 'transport', return_value={}):
            with patch.object(qs, '_transport', return_value={'id': 'failed', 'error': {'message': 'failure'}}):
                with self.assertRaises(QueryError):
                    execution.create_table(self.locator(), [('id', 'INTEGER')], step='create')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM object_bindings').fetchone()[0], 0)
        with patch.object(qs, '_transport', return_value={'id': 'ok'}):
            with self.assertRaises(inv.InventoryError):
                execution.create_table(self.locator(), [('id', 'INTEGER')], step='create2')

    def test_cross_database_view_and_recreated_refs(self):
        execution = self.begin()
        for ref in (self.existing(database=self.databases[1]), self.existing('a_view', kind='VIEW')):
            with self.assertRaises(QueryError):
                execution.select(ref, ['id'], step='read')
        ref = self.existing()
        self.objects[('sales', 'raw', 'observed_data', 'tables')]['table-uuid'] = str(uuid.uuid4())
        with self.assertRaises(QueryError) as caught:
            execution.select(ref, ['id'], step='read')
        self.assertEqual(caught.exception.code, 'STALE_RESOURCE')
        self.assertFalse(self.calls)

    def test_even_admin_cannot_cross_database(self):
        owner = self.fixture.principal('owner')
        execution = self.begin(principal=owner, context=self.context(principal=owner, role='admin'))
        for call in (lambda: execution.create_table(self.locator(database=self.databases[1]), [('id', 'INTEGER')], step='c'),
                     lambda: execution.select(self.existing(database=self.databases[2]), ['id'], step='r')):
            with self.assertRaises(QueryError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'CROSS_DATABASE_TARGET')

    def test_context_unknown_nonready_unauthorized_stale(self):
        context = self.context()
        cases = [replace(context, database_id='missing'), replace(context, binding_revision=99),
                 replace(context, grant_revision=99), replace(context, schema_id=self.schemas[self.databases[1]]['raw'])]
        for index, candidate in enumerate(cases):
            with self.assertRaises(QueryError):
                self.begin(str(index), context=candidate)
        with control_store.connect() as db:
            db.execute("UPDATE databases SET state='REGISTERED' WHERE id=?", (context.database_id,))
        with self.assertRaises(QueryError):
            self.begin('nonready', context=context)
        with control_store.connect() as db:
            db.execute("UPDATE databases SET state='READY' WHERE id=?", (context.database_id,))
            dm.update_grant(db, 'alice', context.database_id, 1, 'revoked')
        with self.assertRaises(QueryError):
            self.begin('unauthorized', context=context)

    def test_revoke_regrant_rejects_step_and_child_result_access(self):
        execution = self.begin()
        page = execution.select(self.existing(), ['id'], step='read')
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.databases[0], 1, 'revoked')
            dm.update_grant(db, 'alice', self.databases[0], 2, 'data_engineer')
        for call in (execution.inspect, lambda: qs.inspect_query(self.principal, page['id'], selected_role='data_engineer')):
            with self.assertRaises(QueryError):
                call()

    def test_role_write_permissions_and_admin_publish(self):
        execution = self.begin()
        marts = self.existing(schema='marts')
        execution.select(marts, ['id'], step='read')
        with self.assertRaises(QueryError):
            execution.insert_rows(marts, ['id'], [(3,)], step='write')
        raw = self.existing('source')
        with self.assertRaises(QueryError):
            execution.rename(raw, self.locator('published', schema='marts'), step='publish')

    def test_fixed_credentials_across_rotation_and_continuation(self):
        execution = self.begin()
        ref = self.existing()
        execution.insert_rows(ref, ['id'], [(1,), (2,)], step='one')
        self.credential = 'Basic rotated-secret'
        execution.insert_rows(ref, ['id'], [(3,), (4,)], step='two')
        self.assertEqual([call[2]['Authorization'] for call in self.calls], ['Basic original-secret'] * 2)
        with control_store.connect() as db:
            self.assertNotIn('original-secret', str([dict(r) for r in db.execute('SELECT * FROM dataops_operations')]))

    def test_replay_and_mismatch_while_draining(self):
        execution = self.begin()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # The DRAINING operation id belongs to the separate configuration ledger.
            dm.record_operation(db, dm.Operation('drain', 'alice', 'test', (), None, 'RUNNING', (), None, (), ()))
            admission.begin_drain(db, warehouse_id='w', operation_id='drain')
        replay = self.begin()
        self.assertEqual(replay.id, execution.id)
        self.assertTrue(replay.replayed)
        with self.assertRaises(QueryError):
            replay.select(self.existing(), ['id'], step='forbidden')
        with self.assertRaises(QueryError) as caught:
            self.begin(payload={'mode': 'changed'})
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(admission.AdmissionDenied):
            self.begin('new')
        execution.insert_rows(self.existing('accepted'), ['id'], [(8,), (9,)], step='accepted')
        with control_store.connect() as db:
            blockers = admission.blockers(db, 'w')
            self.assertEqual([b['kind'] for b in blockers], ['dataops'])
        execution.complete()

    def test_unknown_http_response_no_retry_and_parent_blocks(self):
        from unittest.mock import Mock
        import warehouse_resident as wr
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.connection.shutdown(2)
                self.connection.close()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        process = Mock()
        process.poll.return_value = None
        self.start_patch('warehouse_resident._PERSISTENT_TRINO',
                         dict(url=origin, process=process, kubectl=wr.KUBECTL))
        self.start_patch('warehouse_resident._sql_kubectl', return_value=wr.KUBECTL)
        @contextmanager
        def backend():
            self.assertTrue(wr._borrowable_trino())
            yield origin
        resolver = self.start_patch('warehouse_resident.persistent_trino', side_effect=backend)
        self.assertIsNone(qs._origin)
        execution = self.begin()
        ref = self.existing()
        self.transport_patch.stop()
        with self.assertRaises(QueryError):
            execution.insert_rows(ref, ['id'], [(101,), (203,)], step='lost_response')
        self.assertEqual(execution.inspect()['state'], 'UNKNOWN')
        self.assertEqual(self.begin().inspect()['state'], 'UNKNOWN')
        with self.assertRaises(QueryError):
            execution.insert_rows(ref, ['id'], [(101,), (203,)], step='lost_response')
        self.assertEqual(len(requests), 1)
        resolver.assert_called_once_with()
        process.poll.assert_called()
        with control_store.connect() as db:
            self.assertEqual({b['state'] for b in admission.blockers(db, 'w')}, {'UNKNOWN'})

    def test_lost_process_intent_is_unknown_and_observation_only(self):
        execution = self.begin()
        ref = self.existing()
        with patch.object(qs, '_receive', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                execution.insert_rows(ref, ['id'], [(7,)], step='intent')
        # Simulate the interpreter disappearing, including process-private leases.
        self.clear_pins()
        replay = self.begin()
        snapshot = replay.inspect()
        self.assertEqual(snapshot['state'], 'UNKNOWN')
        self.assertEqual(snapshot['queries'][0]['state'], 'UNKNOWN')
        self.assertFalse(self.calls)

    def test_actual_subprocess_exit_releases_owner_and_never_resends_intent(self):
        context = self.context()
        script = '''
import os, json
from contextlib import contextmanager
import dataops_service as ds, query_service as qs, database_inventory as inv
import data_management as dm, request_auth
from query_context import ResolvedQueryContext
context = ResolvedQueryContext(**json.loads(os.environ['DATAOPS_TEST_CONTEXT']))
principal = request_auth.AuthenticatedPrincipal('alice', 'data_engineer', False)
qs._capture_dataops = lambda p,c: qs._Pin(c, 'http://127.0.0.1:1234', {'X-Trino-User':'fixed','Authorization':'private'}, None)
@contextmanager
def catalog(database, binding):
    def api(path):
        raise inv.InventoryError('NOT_FOUND',404,'missing')
    yield api
inv.catalog_api = catalog
qs._receive = lambda *args, **kwargs: os._exit(23)
operation = ds.begin(principal, context, route='manual-ingest', idempotency_key='actual-crash', payload={'mode':'test'})
operation.create_table(dm.DestinationLocator(context.database_id,context.schema_id,'crash_table'), [('id','INTEGER')],step='crashed-create')
'''
        environment = dict(os.environ, PYTHONPATH=str(Path(ds.__file__).parent),
                           DATAOPS_TEST_CONTEXT=json.dumps(vars(context)))
        result = subprocess.run([sys.executable, '-c', script], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        snapshot = self.begin('actual-crash').inspect()
        self.assertEqual(snapshot['state'], 'UNKNOWN')
        self.assertEqual(snapshot['queries'][0]['state'], 'UNKNOWN')
        self.assertFalse(self.calls)
        with control_store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'w')), 2)

    def test_subprocess_exit_before_first_step_allows_explicit_failed_abandon(self):
        context = self.context()
        script = '''
import os,json
import dataops_service as ds,query_service as qs,request_auth
from query_context import ResolvedQueryContext
context=ResolvedQueryContext(**json.loads(os.environ['DATAOPS_TEST_CONTEXT']))
principal=request_auth.AuthenticatedPrincipal('alice','data_engineer',False)
qs._capture_dataops=lambda p,c: qs._Pin(c,'http://127.0.0.1:1234',{'X-Trino-User':'fixed','Authorization':'private'},None)
ds.begin(principal,context,route='manual-ingest',idempotency_key='before-first-step',payload={'mode':'test'})
os._exit(24)
'''
        environment = dict(os.environ, PYTHONPATH=str(Path(ds.__file__).parent),
                           DATAOPS_TEST_CONTEXT=json.dumps(vars(context)))
        result = subprocess.run([sys.executable, '-c', script], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 24, result.stderr)
        execution = self.begin('before-first-step')
        snapshot = execution.inspect()
        self.assertEqual(snapshot['state'], 'UNKNOWN')
        self.assertEqual(snapshot['queries'], [])
        with control_store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'w')), 1)
        result = execution.reconcile_failure()
        self.assertEqual(result['state'], 'FAILED')
        self.assertFalse(result['committed'])
        self.assertIsNone(result['result'])
        self.assertFalse(self.calls)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
            parent = db.execute("SELECT evidence_ref FROM warehouse_admissions WHERE kind='dataops'").fetchone()
            self.assertTrue(parent['evidence_ref'])

    def test_parent_reconciliation_requires_definite_children_and_preserves_commit(self):
        execution = self.begin()
        ref = self.existing()
        execution.insert_rows(ref, ['id'], [(11,), (12,)], step='committed')
        execution.record_commit({'rows': 2})
        with patch.object(qs, '_transport', side_effect=QueryError('QUERY_BACKEND_FAILED', 502)):
            with self.assertRaises(QueryError):
                execution.select(ref, ['id'], step='uncertain_read')
        with self.assertRaises(QueryError) as caught:
            execution.reconcile_failure()
        self.assertEqual(caught.exception.code, 'UNRESOLVED_CHILD_QUERIES')
        query_id = execution.inspect()['queries'][-1]['id']
        def observer(expected):
            return [dict(id='observed_final', source=expected['source'], user=expected['backend_username'],
                         sql=expected['sql'], state='FAILED')]
        qs.reconcile_query(self.principal, query_id, selected_role='data_engineer', observer=observer)
        outcome = execution.reconcile_failure()
        self.assertEqual(outcome['state'], 'FAILED')
        self.assertTrue(outcome['committed'])
        self.assertEqual(outcome['result'], {'rows': 2})
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_owned_cleanup_never_unrelated_or_recreated(self):
        execution = self.begin()
        unrelated = self.existing()
        with self.assertRaises(QueryError) as caught:
            execution.cleanup(unrelated, step='bad')
        self.assertEqual(caught.exception.code, 'OBJECT_NOT_OWNED')
        ref = execution.create_stage(self.schemas[self.databases[0]]['sandbox'], [('id', 'BIGINT')], step='stage')
        with control_store.connect() as db:
            obj = dm.get_object(db, ref.object_id)
        self.objects[('sales', 'sandbox', obj.physical_name, 'tables')]['table-uuid'] = str(uuid.uuid4())
        with self.assertRaises(QueryError):
            execution.cleanup(ref, step='recreated')
        self.assertFalse(any(call[1].startswith('DROP') for call in self.calls))

    def test_successful_commit_retained_after_cleanup_failure(self):
        execution = self.begin()
        ref = execution.create_stage(self.schemas[self.databases[0]]['raw'], [('id', 'INTEGER')], step='stage')
        execution.insert_rows(ref, ['id'], [(5,), (6,)], step='insert')
        execution.record_commit({'rows': 2})
        with patch.object(qs, '_transport', return_value={'id': 'cleanup_error', 'error': {'message': 'no'}}):
            with self.assertRaises(QueryError):
                execution.cleanup(ref, step='cleanup')
        result = execution.complete()
        self.assertTrue(result['committed'])
        self.assertEqual(result['result'], {'rows': 2})
        self.assertEqual(result['cleanup_error'], 'DATAOPS_STEP_FAILED')

    def test_failed_or_canceled_work_cannot_claim_success(self):
        execution = self.begin()
        ref = self.existing()
        with patch.object(qs, '_transport', return_value={'id': 'failed', 'error': {'message': 'no'}}):
            with self.assertRaises(QueryError):
                execution.insert_rows(ref, ['id'], [(7,)], step='failed')
        for call in (lambda: execution.complete(committed=True), lambda: execution.record_commit({'rows': 1})):
            with self.assertRaises(QueryError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'OPERATION_NOT_SUCCESSFUL')
        self.assertEqual(execution.fail()['state'], 'FAILED')
        empty = self.begin('empty')
        self.assertEqual(empty.cancel(), [])
        self.assertEqual(empty.inspect()['state'], 'CANCELED')
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_nonowner_cleanup_cannot_mutate_error_or_outcome(self):
        execution = self.begin()
        ref = self.existing()
        attacker = ds.Execution(self.fixture.principal('owner'), execution.id)
        with self.assertRaises(QueryError) as caught:
            attacker.cleanup(ref, step='bad')
        self.assertEqual(caught.exception.status, 404)
        self.assertIsNone(execution.inspect()['cleanup_error'])
        with self.assertRaises(QueryError):
            ds.Execution(('alice', 'admin'), execution.id)

    def test_changed_binding_blocks_later_step_and_result(self):
        execution = self.begin()
        ref = self.existing()
        page = execution.select(ref, ['id'], step='read')
        with control_store.connect() as db:
            db.execute('UPDATE catalog_bindings SET desired_revision=2 WHERE database_id=?', (self.databases[0],))
        for call in (execution.inspect, lambda: execution.select(ref, ['id'], step='second'),
                     lambda: qs.inspect_query(self.principal, page['id'], selected_role='data_engineer')):
            with self.assertRaises(QueryError):
                call()

    def test_admin_promotion_returns_new_unowned_ref(self):
        owner = self.fixture.principal('owner')
        execution = self.begin(principal=owner, context=self.context(principal=owner, role='admin'))
        source = execution.create_stage(self.schemas[self.databases[0]]['sandbox'], [('id', 'INTEGER')], step='stage')
        original_transport = self.transport
        def promote(url, method, pin, body=None):
            result = original_transport(url, method, pin, body)
            sql = body.decode() if body else ''
            if sql.startswith('ALTER TABLE'):
                names = re.findall(r'"([^"]+)"', sql)
                metadata = self.objects.pop((*names[:3], 'tables'))
                self.objects[(*names[3:6], 'tables')] = metadata
            return result
        with patch.object(qs, '_transport', side_effect=promote):
            promoted = execution.rename(source, self.locator('final_table', schema='marts'), step='promote')
        self.assertNotEqual(promoted.object_id, source.object_id)
        with self.assertRaises(QueryError):
            execution.select(source, ['id'], step='stale')
        with self.assertRaises(QueryError) as caught:
            execution.cleanup(promoted, step='must_keep_final')
        self.assertEqual(caught.exception.code, 'OBJECT_NOT_OWNED')
        execution.select(promoted, ['id'], step='read_final')
        execution.complete(committed=True)

    def test_successful_owned_cleanup_and_duplicate_step_not_resent(self):
        execution = self.begin()
        ref = execution.create_stage(self.schemas[self.databases[0]]['raw'], [('value', 'VARCHAR')], step='stage')
        execution.insert_rows(ref, ['value'], [('x',)], step='insert')
        count = len(self.calls)
        with self.assertRaises(QueryError):
            execution.insert_rows(ref, ['value'], [('changed',)], step='insert')
        self.assertEqual(len(self.calls), count)
        execution.cleanup(ref, step='cleanup')
        with control_store.connect() as db:
            self.assertIsNone(dm.get_object(db, ref.object_id))
        execution.complete()

    def test_literal_and_identifier_validation_public_drop_guard(self):
        from datetime import datetime, timezone
        import warehouse_resident as wr
        self.assertEqual(ds._literal(Decimal('12345678901234567890.001')), "DECIMAL '12345678901234567890.001'")
        for value in (float('nan'), object(), datetime.now(timezone.utc), 'x\x00y'):
            with self.assertRaises(ValueError):
                ds._literal(value)
        execution = self.begin()
        for name in ('bad;drop', 'UPPER', 'a.b'):
            with self.assertRaises(ValueError):
                execution.create_table(self.locator(name), [('id', 'INTEGER')], step='bad')
        with self.assertRaises(ValueError):
            wr.check_sql_policy('DROP TABLE sales.raw.anything', 'sandbox-write', 'data_engineer', context=self.context())

    def test_same_actor_query_owner_and_pinned_cancel_continuation(self):
        execution = self.begin()
        ref = self.existing()
        count = [0]
        def pages(url, method, pin, body=None):
            self.calls.append((method, body, dict(pin.headers)))
            count[0] += 1
            if method == 'POST':
                return {'id': 'paged', 'nextUri': 'http://127.0.0.1:1234/v1/statement/executing/paged/1'}
            self.credential = 'rotated'
            return {'id': 'paged', 'data': [[1], [2], [3]]}
        with patch.object(qs, '_transport', side_effect=pages):
            page = execution.select(ref, ['id'], step='read')
        self.assertEqual(page['data'], [[1], [2], [3]])
        self.assertEqual([c[2]['Authorization'] for c in self.calls], ['Basic original-secret'] * 2)
        owner = self.fixture.principal('owner')
        with self.assertRaises(QueryError):
            qs.inspect_query(owner, page['id'], selected_role='data_engineer')
        self.assertEqual(qs.cancel(self.principal, page['id'], selected_role='data_engineer')['state'], 'FINISHED')

    def test_operation_cancel_interrupts_endless_pages_with_original_credentials(self):
        execution = self.begin()
        ref = self.existing()
        reading, release = threading.Event(), threading.Event()
        calls, errors, cancellations = [], [], []
        def transport(url, method, pin, body=None):
            calls.append((method, pin.headers['Authorization']))
            if method == 'DELETE':
                return {}
            if method == 'GET':
                reading.set()
                release.wait(2)
                time.sleep(.01)
            return {'id': 'forever', 'nextUri': 'http://127.0.0.1:1234/v1/statement/executing/forever/1'}
        def run():
            try:
                execution.select(ref, ['id'], step='read_forever')
            except QueryError as error:
                errors.append(error.code)
        with patch.object(qs, '_transport', side_effect=transport):
            reader = threading.Thread(target=run, daemon=True)
            reader.start()
            self.assertTrue(reading.wait(2))
            self.credential = 'Basic replacement'
            canceler = threading.Thread(target=lambda: cancellations.extend(execution.cancel()), daemon=True)
            canceler.start()
            release.set()
            canceler.join(3)
            reader.join(3)
        self.assertFalse(reader.is_alive())
        self.assertFalse(canceler.is_alive())
        self.assertTrue(any(method == 'DELETE' for method, _ in calls))
        self.assertEqual({credential for _, credential in calls}, {'Basic original-secret'})
        self.assertEqual(execution.inspect()['state'], 'CANCELED')
        self.assertEqual(cancellations[0]['state'], 'CANCELED')

    def running_children(self, execution, count=1):
        frozen, parent, _, _ = ds._live[execution.id]
        with patch.object(qs, '_transport', return_value=dict(id='active',
                nextUri='http://127.0.0.1:1234/v1/statement/executing/active/1')):
            return [qs._submit_dataops(self.principal, ds._Statement('select', 'SELECT 1'),
                frozen, parent, execution.id, 'child-' + str(index))['id'] for index in range(count)]

    def test_cancel_unknown_parent_preserves_children_and_drain_blocker(self):
        execution = self.begin()
        first, second = self.running_children(execution, 2)
        qs._state(first, 'UNKNOWN')
        with patch.object(qs, '_transport') as transport:
            results = execution.cancel()
        transport.assert_not_called()
        self.assertEqual([r['state'] for r in results], ['UNKNOWN', 'RUNNING'])
        self.assertEqual(qs._row(second)['state'], 'RUNNING')
        self.assertEqual(execution._row()['state'], 'UNKNOWN')
        with control_store.connect() as db:
            handle = admission.get_for_subject(db, kind='dataops', subject_id=execution.id,
                                               warehouse_id=self.context().warehouse_id)
            self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE id=?',
                                       (handle.id,)).fetchone()[0], 'UNKNOWN')

    def test_cancel_multiple_running_children_after_grant_loss_uses_pins(self):
        execution = self.begin()
        children = self.running_children(execution, 2)
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                       (self.principal.principal_id, self.context().database_id))
        self.credential = 'Basic rotated'
        calls = []
        def transport(url, method, pin, body=None):
            calls.append((method, pin.headers['Authorization']))
            return {}
        with patch.object(qs, '_transport', side_effect=transport):
            results = execution.cancel()
        self.assertEqual([r['state'] for r in results], ['CANCELED', 'CANCELED'])
        self.assertEqual(calls, [('DELETE', 'Basic original-secret')] * 2)
        self.assertEqual(execution._row()['state'], 'CANCELED')
        self.assertTrue(all(qs._row(q)['state'] == 'CANCELED' for q in children))


if __name__ == '__main__':
    unittest.main()
