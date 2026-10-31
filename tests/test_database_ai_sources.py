"""Latest original SQL text with isolated SQLite and both HTTP adapters."""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from email.message import Message
from http.client import HTTPResponse
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
# Isolate state before importing any handlers or legacy Bears configuration.
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai as a1
        import db_ai_sources as sources
        import ai_routes
        import control_store as store
        import data_management as dm
        import request_auth as auth
        import warehouse_resident as wr
        import logical_registry as lr
        import workbench_server as wb
from query_context import QueryError


class SourceTests(unittest.TestCase):
    # Reuse the actual two-database, three-actor A1 setup, without its tests.
    patch = a1.DatabaseAITests.patch
    ctx = a1.DatabaseAITests.ctx
    metadata = staticmethod(a1.DatabaseAITests.metadata)
    catalog = a1.DatabaseAITests.catalog
    assert_error = a1.DatabaseAITests.assert_error

    def setUp(self):
        a1.DatabaseAITests.setUp(self)
        for target in ('db_ai._capture', 'db_ai._complete', 'db_ai._config',
                'database_inventory.catalog_api', 'database_inventory.resolve_object',
                'database_inventory.object_detail', 'query_service._state',
                'query_service._acquire_query_owner', 'query_service._best_cancel',
                'query_service.inspect_query', 'query_service.history',
                'warehouse_lifecycle.ensure_awake', 'trino_auth.prepare_backend_v2',
                'logical_registry._credentials', 'bears_resident.resolve_api_key'):
            self.patch(patch(target, side_effect=AssertionError(target)))

    def record(self, ident='ordinary', *, context=None, actor='alice', sql='SELECT * FROM orders',
               state='FINISHED', mode='read-only', created='2026-10-31T10:00:00Z', username='private-user'):
        c = context or self.context
        with store.connect() as db:
            dm.record_query(db, dm.QueryRecord(ident, actor,
                dm.QueryContext(c.database_id, c.schema_id, c.warehouse_id, c.generation_id), state, created))
            dm.record_query_execution(db, dm.QueryExecutionContext(ident, c.selected_role, mode,
                c.sql_catalog, c.sql_schema, c.binding_revision, c.grant_revision, username,
                'SECRET-CREDENTIAL', None, 'private-request', 'private-worksheet', sql))
            db.execute('UPDATE query_execution_contexts SET backend_query_id=? WHERE query_id=?',
                ('SECRET-BACKEND', ident))
        return ident

    def latest(self, context=None, principal=None):
        return sources.latest(principal or self.principal, context or self.context)

    def snapshot(self):
        with store.connect() as db:
            return list(db.iterdump())

    def test_original_projection_and_all_historical_states_are_read_only(self):
        sql = "-- original comment\nWITH a AS (SELECT 'literal' x FROM orders) SELECT * FROM a;"
        ident = self.record(sql=sql)
        for state in ('QUEUED', 'RUNNING', 'FINISHED', 'FAILED', 'CANCELED', 'UNKNOWN'):
            with self.subTest(state=state):
                with store.connect() as db:
                    db.execute('UPDATE query_records SET state=? WHERE id=?', (state, ident))
                before = self.snapshot()
                self.assertEqual(self.latest(), dict(query_id=ident, sql=sql, state=state,
                    created_at='2026-10-31T10:00:00Z', context=self.context.public_metadata()))
                self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.catalog_calls)

    def test_source_connections_forbid_sqlite_writes(self):
        self.record(state='RUNNING')
        connect = store.connect
        @contextmanager
        def read_only():
            with connect() as db:
                def authorize(action, *args):
                    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                                  sqlite3.SQLITE_DELETE):
                        raise AssertionError('source attempted a state write')
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                yield db
        with patch.object(store, 'connect', read_only):
            self.assertEqual(self.latest()['state'], 'RUNNING')

    def test_empty_actor_database_schema_role_isolation_and_no_admin_override(self):
        self.assertIsNone(self.latest())
        self.record()
        for context in (self.ctx(database=self.databases[1]), self.ctx(schema='sandbox'), self.ctx('admin')):
            self.assertIsNone(self.latest(context=context))
        self.assertIsNone(self.latest(principal=self.bob))
        admin = self.ctx('admin', principal=self.owner)
        self.assertIsNone(self.latest(context=admin, principal=self.owner))
        self.record('alice-admin', context=admin)
        self.assertIsNone(self.latest(context=admin, principal=self.owner))

    def test_every_frozen_context_field_matches_including_nullable_grant(self):
        self.record()
        fields = {'database_id': 'query_records', 'schema_id': 'query_records',
            'warehouse_id': 'query_records', 'generation_id': 'query_records',
            **{k: 'query_execution_contexts' for k in ('sql_catalog', 'sql_schema',
                'selected_role', 'binding_revision', 'grant_revision')}}
        for field, table in fields.items():
            original = getattr(self.context, field)
            for changed in ([None, 999] if field == 'grant_revision' else
                            [999] if field == 'binding_revision' else ['other']):
                with self.subTest(field=field, changed=changed):
                    # Change frozen provenance only, including retired FK values.
                    with store.connect() as db:
                        db.execute('PRAGMA foreign_keys=OFF')
                        db.execute(f'UPDATE {table} SET {field}=?', (changed,))
                    self.assertIsNone(self.latest())
                    with store.connect() as db:
                        db.execute('PRAGMA foreign_keys=OFF')
                        db.execute(f'UPDATE {table} SET {field}=?', (original,))
        with store.connect() as db:
            db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice'")
        self.assertIsNone(self.latest(context=self.ctx()))
        self.assert_error('CONTEXT_MISMATCH', self.latest)

    def test_platform_admin_nullable_grant_matches_exactly(self):
        c = self.ctx('admin', principal=self.owner)
        self.assertIsNone(c.grant_revision)
        self.record(actor='owner', context=c)
        self.assertEqual(self.latest(c, self.owner)['context'], c.public_metadata())
        with store.connect() as db:
            db.execute('UPDATE query_execution_contexts SET grant_revision=1')
        self.assertIsNone(self.latest(c, self.owner))

    def test_latest_created_then_insertion_order_and_structural_skip(self):
        self.record('z-first', sql='SELECT 1')
        self.record('a-second', sql='SELECT 2 UNION ALL SELECT 3')
        self.record('older-inserted-last', created='2020', sql='SELECT 0')
        self.assertEqual(self.latest()['query_id'], 'a-second')
        for i, sql in enumerate(('EXPLAIN SELECT 1', 'INSERT INTO orders VALUES (1)',
                'DELETE FROM orders', 'SELECT mystery_function(1)', 'garbage syntax',
                'SELECT 1; SELECT 2', 'SELECT ' + '1' * 32769)):
            self.record('skip-' + str(i), sql=sql, created='2099')
        self.assertEqual(self.latest()['query_id'], 'a-second')

    def test_historical_preparse_never_logs_private_source(self):
        valid = "WITH a AS (SELECT CAST('private_valid_91c7' AS VARCHAR) x) SELECT TRIM(x) FROM a"
        self.record('older-valid', sql=valid, created='2020')
        cases = (
            ("EXPLAIN SELECT 'private_explain_91c7'", 'private_explain_91c7'),
            ("EXECUTE private_statement USING 'private_command_91c7'", 'private_command_91c7'),
            ("SELECT 'private_malformed_91c7' FROM (", 'private_malformed_91c7'),
            ("WITH a AS (EXPLAIN SELECT 'private_nested_91c7') SELECT * FROM a", 'private_nested_91c7'),
            ("SELECT 1; EXPLAIN SELECT 'private_second_91c7'", 'private_second_91c7'),
            ("SELECT 1; SELECT 'private_second_bad_91c7' FROM (", 'private_second_bad_91c7'),
        )
        logs, stdout, stderr = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        logger = logging.getLogger()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        with redirect_stdout(stdout), redirect_stderr(stderr), \
                patch.object(sources.ai, '_parse', wraps=sources.ai._parse) as parse:
            for index, (sql, _) in enumerate(cases):
                with self.subTest(candidate=index):
                    self.record('bad-' + str(index), sql=sql, created='2099')
                    parse.reset_mock()
                    self.assertEqual(self.latest()['query_id'], 'older-valid')
                    # Only the older valid statement reaches A1 policy/generation.
                    parse.assert_called_once_with(valid, self.context)
        captured = logs.getvalue() + stdout.getvalue() + stderr.getvalue()
        for marker in ('private_valid_91c7', *(marker for _, marker in cases)):
            self.assertNotIn(marker, captured)
        self.assertFalse(self.catalog_calls)

    def test_existing_ledger_ai_dataops_parent_child_internal_exclusions(self):
        self.record('ordinary')
        for name in ('dataops', 'ai', 'request', 'child', 'internal', 'parent'):
            self.record(name, created='2099', sql='SELECT 123')
        self.record('dataops-mode', mode='dataops', created='2099')
        with store.connect() as db:
            db.execute("""INSERT INTO dataops_operations VALUES
                ('op','alice','read','key','{}','fingerprint','{}','user','ref','ACTIVE',0,NULL,NULL,'now')""")
            db.execute("INSERT INTO dataops_queries(operation_id,step,query_id,action) VALUES ('op','read','dataops','read')")
            db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)',
                ('explanation', 'alice', 'key', '{}', '{}', '[]', json.dumps({'plan': {'query_id': 'ai'}})))
            db.execute("UPDATE query_execution_contexts SET request_id='explanation' WHERE query_id='request'")
            db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','OPEN',NULL,1,'g-sales')")
            for ident, kind, subject, parent in (
                    ('root', 'dataops', 'job', None), ('child-admission', 'query', 'child', 'root'),
                    ('internal-admission', 'internal', 'internal', None),
                    ('parent-admission', 'query', 'parent', None),
                    ('nested', 'query', 'nested-query', 'parent-admission')):
                db.execute("""INSERT INTO warehouse_admissions
                    (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,state,created_at)
                    VALUES (?,'LOCAL','alice',?,?,?,'g-sales','FINISHED','now')""",
                    (ident, kind, subject, parent))
        self.assertEqual(self.latest()['query_id'], 'ordinary')

    def test_primary_and_all_reference_schema_authority_no_fallback(self):
        self.record('older', sql='SELECT 1', created='2020')
        self.record('newest', sql='SELECT * FROM marts.orders JOIN sandbox.orders s ON TRUE')
        for schema in ('raw', 'marts', 'sandbox'):
            with self.subTest(schema=schema):
                ident = self.schemas[self.database.id][schema].id
                with store.connect() as db:
                    db.execute("UPDATE schemas SET purpose='hidden' WHERE id=?", (ident,))
                self.assert_error('SQL_PERMISSION_DENIED', self.latest)
                with store.connect() as db:
                    db.execute('UPDATE schemas SET purpose=? WHERE id=?', (schema, ident))
        with store.connect() as db:
            db.execute('UPDATE query_execution_contexts SET sql=? WHERE query_id=?',
                ('SELECT * FROM "marts".orders JOIN SANDBOX.orders s ON TRUE', 'newest'))
        self.assertEqual(self.latest()['query_id'], 'newest')

    def test_reference_role_rights_and_missing_schema_refuse(self):
        context = self.ctx('bi_viewer', schema='marts')
        self.record(context=context, sql='SELECT * FROM raw.orders')
        self.assert_error('SQL_PERMISSION_DENIED', lambda: self.latest(context))
        with store.connect() as db:
            db.execute('UPDATE query_execution_contexts SET sql=?', ('SELECT * FROM staging.orders',))
            db.execute('DELETE FROM schemas WHERE id=?', (self.schemas[self.database.id]['staging'].id,))
        self.assert_error('SQL_PERMISSION_DENIED', lambda: self.latest(context))

    def test_schema_grant_and_account_loss_after_candidate_check_refuses(self):
        self.record('older', created='2020', sql='SELECT 1')
        self.record('newest', sql='SELECT * FROM marts.orders JOIN sandbox.orders s ON TRUE')
        original = sources._authority
        for loss in ('raw', 'marts', 'sandbox', 'grant', 'account'):
            with self.subTest(loss=loss):
                calls = 0
                def authority(*args, **kwargs):
                    nonlocal calls
                    original(*args, **kwargs)
                    calls += 1
                    if calls == 2:
                        with store.connect() as db:
                            if loss in ('raw', 'marts', 'sandbox'):
                                db.execute("UPDATE schemas SET purpose='hidden' WHERE id=?",
                                    (self.schemas[self.database.id][loss].id,))
                            elif loss == 'grant':
                                db.execute("UPDATE effective_grants SET maximum_role='revoked' WHERE principal_id='alice'")
                            else:
                                self.users['alice']['role'] = 'bi_viewer'
                with patch.object(sources, '_authority', side_effect=authority):
                    self.assert_error('SQL_PERMISSION_DENIED' if loss in ('raw', 'marts', 'sandbox')
                        else 'DB_ACCESS_REVOKED', self.latest)
                with store.connect() as db:
                    for name in ('raw', 'marts', 'sandbox'):
                        db.execute('UPDATE schemas SET purpose=? WHERE id=?',
                            (name, self.schemas[self.database.id][name].id))
                    db.execute("UPDATE effective_grants SET maximum_role='admin' WHERE principal_id='alice'")
                self.users['alice']['role'] = 'admin'

    def test_invalid_parent_context_and_account_fail_without_sql(self):
        self.record(sql="SELECT 'private literal'")
        for context, code in ((replace(self.context, binding_revision=2), 'STALE_BINDING'),
                (replace(self.context, sql_catalog='other'), 'CONTEXT_MISMATCH'),
                (replace(self.context, schema_id='absent'), 'UNKNOWN_CONTEXT')):
            self.assert_error(code, lambda: self.latest(context=context))
        del self.users['alice']
        self.assert_error('DB_ACCESS_REVOKED', self.latest)

    def test_adapter_strict_get_framing_and_role(self):
        path = '/api/v1/bears/query-sources/latest'
        params = {'context': [json.dumps(self.context.public_metadata())]}
        h = Mock(path=path, headers=Message())
        ai_routes.dispatch(h, 'GET', path, params, self.principal, 'data_engineer')
        self.assertEqual(h.respond.call_args.args,
            ({'context': self.context.public_metadata(), 'source': None}, 200))
        self.record()
        self.assertTrue(ai_routes.dispatch(h, 'GET', path, params, self.principal, 'data_engineer'))
        response, status = h.respond.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(set(response), {'context', 'source'})
        self.assertEqual(response['context'], response['source']['context'])
        for headers, query, role in (
                ([('Content-Length', '1')], params, 'data_engineer'),
                ([('Content-Length', '-1')], params, 'data_engineer'),
                ([('Content-Length', '0'), ('Content-Length', '0')], params, 'data_engineer'),
                ([('Transfer-Encoding', '')], params, 'data_engineer'),
                ([], {}, 'data_engineer'), ([], dict(params, query_id=['ordinary']), 'data_engineer'),
                ([], {'context': params['context'] * 2}, 'data_engineer'), ([], params, 'admin')):
            h.headers = Message()
            for key, value in headers:
                h.headers[key] = value
            with self.assertRaises(ValueError):
                ai_routes.dispatch(h, 'GET', path, query, self.principal, role)
        h.headers = Message()
        raw = params['context'][0]
        for invalid in (raw[:-1] + ',"schema_id":"duplicate"}',
                json.dumps(dict(self.context.public_metadata(), extra='forbidden')),
                json.dumps({k: v for k, v in self.context.public_metadata().items() if k != 'grant_revision'})):
            with self.assertRaises(ValueError):
                ai_routes.dispatch(h, 'GET', path, {'context': [invalid]}, self.principal, 'data_engineer')
        for method in ('POST', 'DELETE', 'PUT'):
            self.assert_error('NOT_FOUND', lambda: ai_routes.dispatch(h, method, path, params,
                self.principal, 'data_engineer'))

    def test_both_real_http_handlers_strict_contract_and_origin(self):
        import runtime_profile
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch('warehouse_resident._profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        self.assertEqual(len(handlers), 2)
        class Connection:
            def __init__(self, raw): self.incoming, self.outgoing = io.BytesIO(raw), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, raw): self.outgoing.write(raw)
        self.record()
        path = '/api/v1/bears/query-sources/latest'
        query = '?' + urlencode({'context': json.dumps(self.context.public_metadata())})
        cases = [('GET', query, '', '', 200), ('GET', query, 'Content-Length: 0\r\n', '', 200),
            ('GET', query, 'Content-Length: 1\r\n', 'x', 400),
            ('GET', query, 'Content-Length: nope\r\n', '', 400),
            ('GET', query, 'Content-Length: 0\r\nContent-Length: 0\r\n', '', 400),
            ('GET', query, 'Transfer-Encoding: chunked\r\n', '0\r\n\r\n', 400),
            ('GET', query + '&context=%7B%7D', '', '', 400),
            ('GET', query + '&query_id=ordinary', '', '', 400),
            ('GET', '', '', '', 400), ('POST', query, 'Content-Length: 0\r\n', '', 404),
            ('POST', query, 'Origin: http://evil.invalid\r\nContent-Length: 0\r\n', '', 403)]
        for handler in handlers:
            for method, suffix, extra, body, status in cases:
                with self.subTest(surface=handler.__name__, method=method, extra=extra, suffix=suffix[-25:]):
                    raw = (method + ' ' + path + suffix + ' HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                        'Connection: close\r\nX-Shirokuma-Role: data_engineer\r\n' + extra + '\r\n' + body).encode()
                    conn = Connection(raw)
                    with patch.object(runtime_profile, 'active', return_value=True), \
                            patch.object(auth, 'authenticate_principal', return_value=self.principal), \
                            patch.object(handler, 'log_message'):
                        handler(conn, ('127.0.0.1', 1), SimpleNamespace())
                    response = HTTPResponse(Connection(conn.outgoing.getvalue()))
                    response.begin()
                    reply = json.loads(response.read())
                    self.assertEqual(response.status, status, reply)
                    if status == 200:
                        self.assertEqual(set(reply), {'context', 'source'})
                        self.assertEqual(reply['source']['query_id'], 'ordinary')


class LogicalSourceTests(unittest.TestCase):
    def test_retired_identity_and_changed_username_never_fallback(self):
        import test_logical_registry as fixtures
        fixture = fixtures.LogicalRegistryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.setup_ready()
        fixture.db.execute("UPDATE warehouse_config_generations SET database_revisions='[]',grant_revisions='[]',configuration_refs='[]'")
        fixture.grant()
        identity = fixture.activate(fixture.candidate())
        fixture.db.execute("INSERT INTO schemas VALUES ('marts','db','marts','Marts','marts','test',1)")
        fixture.db.commit()
        with tempfile.TemporaryDirectory() as root:
            with sqlite3.connect(Path(root) / 'jobs.sqlite3') as target:
                fixture.db.backup(target)
            with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': root}, clear=True), \
                    patch('runtime_profile.current', return_value=None), \
                    patch.object(auth, '_principal_accounts', return_value={'alice': {'role': 'admin', 'platform_admin': False}}), \
                    patch.object(lr, '_credentials', side_effect=AssertionError('credentials')):
                from query_context import resolve_context
                principal = auth.AuthenticatedPrincipal('alice', 'admin', False)
                context = resolve_context(principal, 'db', 'marts', 'bi_viewer', expected_binding_revision=1)
                for ident, username, created in (('old', identity['backend_username'], '2020'),
                        ('latest', 'retired-username', '2099')):
                    with store.connect() as db:
                        db.execute("INSERT INTO query_records VALUES (?,'alice','db','marts','LOCAL','foundation','UNKNOWN',?)", (ident, created))
                        db.execute("INSERT INTO query_execution_contexts VALUES (?,'bi_viewer','read-only','db','marts',1,1,?,'pin',NULL,NULL,NULL,'SELECT 1')", (ident, username))
                with self.assertRaises(QueryError) as error:
                    sources.latest(principal, context)
                self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
                with store.connect() as db:
                    db.execute('UPDATE query_execution_contexts SET backend_username=?', (identity['backend_username'],))
                self.assertEqual(sources.latest(principal, context)['query_id'], 'latest')
                with store.connect() as db:
                    db.execute("UPDATE logical_backend_identities SET state='REVOKED'")
                with self.assertRaises(QueryError) as error:
                    sources.latest(principal, context)
                self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')


if __name__ == '__main__':
    unittest.main()
