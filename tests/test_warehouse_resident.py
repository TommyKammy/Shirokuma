import importlib.util
import io
import json
import http.client
import http.server
import threading
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "warehouse_resident", Path(__file__).resolve().parents[1] / "tools/local_experiment/warehouse_resident.py")
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class ResidentWarehouseTests(unittest.TestCase):
    def test_three_tier_role_resolution(self):
        for user in ('bi_reader', 'powerbi', 'bi_team', 'POWERBI'):
            for role in (None, 'admin', 'editor'):
                self.assertEqual(w.resolve_role(user, role), 'bi_viewer')
        for role in ('bi_viewer', 'viewer', 'bi_reader'):
            self.assertEqual(w.resolve_role('person', role), 'bi_viewer')
        for role in ('data_engineer', 'editor'):
            self.assertEqual(w.resolve_role('person', role), 'data_engineer')
        for role in (None, 'admin', 'unknown'):
            self.assertEqual(w.resolve_role('person', role), 'admin')

    def test_role_sql_permissions(self):
        self.assertEqual(w.check_sql_policy('SELECT * FROM marts.t', role='bi_viewer'), 'SELECT * FROM marts.t')
        for mode in ('sandbox-write', 'admin-write'):
            with self.assertRaises(ValueError):
                w.check_sql_policy('SELECT 1', mode=mode, role='bi_viewer')
        for sql in ('SELECT * FROM iceberg.raw.t', 'INSERT INTO iceberg.sandbox.t VALUES (1)'):
            with self.assertRaises(ValueError):
                w.check_sql_policy(sql, role='bi_viewer')
        for schema in ('raw', 'sandbox'):
            sql = f'CREATE TABLE iceberg.{schema}.t AS SELECT 1'
            self.assertEqual(w.check_sql_policy(sql, mode='sandbox-write', role='data_engineer'), sql)
        for mode, sql in [('admin-write', 'SELECT 1'),
                          ('sandbox-write', 'CREATE TABLE iceberg.marts.t AS SELECT 1'),
                          ('sandbox-write', 'CREATE TABLE iceberg.staging.t AS SELECT 1')]:
            with self.assertRaises(ValueError):
                w.check_sql_policy(sql, mode=mode, role='data_engineer')

    def test_bi_policy(self):
        allowed = ['SELECT 1', 'SELECT count(*) FROM iceberg.marts.revenue',
                   'SELECT * FROM tpch_tiny.orders', 'SELECT * FROM information_schema.tables',
                   'SELECT * FROM system.runtime.nodes', 'SHOW CATALOGS',
                   'SHOW TABLES FROM iceberg.marts', 'DESCRIBE iceberg.marts.revenue',
                   "SELECT 'iceberg.raw.t' /* iceberg.sandbox.t */ -- iceberg.staging.t\n",
                   'WITH x AS (SELECT * FROM iceberg.marts.t) SELECT * FROM x']
        for user in ('bi_reader', 'POWERBI', 'Bi_analyst'):
            for sql in allowed:
                with self.subTest(user=user, sql=sql):
                    self.assertEqual(w.check_bi_policy(sql, user), w.check_sql_policy(sql))
            for schema in ('raw', 'sandbox', 'staging'):
                for target in (f'iceberg.{schema}.t', f'"ICEBERG" . "{schema.upper()}" . "t"',
                               f'iceberg /* ignored */ . {schema}.t'):
                    for sql in (f'SELECT * FROM {target}',
                                f'SELECT * FROM iceberg.marts.t JOIN {target} ON true',
                                f'WITH x AS (SELECT * FROM {target}) SELECT * FROM x',
                                f'DESCRIBE {target}'):
                        with self.subTest(user=user, sql=sql), self.assertRaisesRegex(
                                ValueError, 'query restricted to iceberg.marts and metadata'):
                            w.check_bi_policy(sql, user)
            for sql in ('INSERT INTO iceberg.marts.t VALUES (1)', 'DROP TABLE iceberg.marts.t',
                        'SELECT 1; SELECT 2'):
                with self.assertRaises(ValueError):
                    w.check_bi_policy(sql, user)
        sql = 'INSERT INTO iceberg.raw.t VALUES (1)'
        self.assertEqual(w.check_bi_policy(sql, 'engineer'), sql)
        self.assertEqual(w.check_bi_policy('SELECT 1;  ', 'powerbi'), 'SELECT 1')

    def endpoint_request(self, method, path, sql=None, headers=None, replicas=1,
                         backend_status=200, payload=None, failure=None):
        server = http.server.HTTPServer(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
        client = http.client.HTTPConnection(*server.server_address, timeout=5)
        backend = Mock()
        response = backend.getresponse.return_value
        response.status = backend_status
        response.read.return_value = json.dumps(payload or {'data': [[42]]}).encode()
        response.getheaders.return_value = [('Content-Type', 'application/json'),
                                            ('X-Trino-Set-Catalog', 'iceberg')]
        response.getheader.return_value = ''
        backend.request.side_effect = failure
        # Keep a real listening server, while allowing serve_endpoint to install
        # its handler and exercise one complete HTTP exchange synchronously.
        def factory(address, handler):
            server.RequestHandlerClass = handler
            return server
        with patch.object(w.http.server, 'HTTPServer', side_effect=factory), \
             patch.object(w.http.client, 'HTTPConnection', return_value=backend), \
             patch.object(w, 'trino', return_value=nullcontext('http://127.0.0.1:1234')) as trino, \
             patch.object(w, 'status', return_value={'replicas': replicas}) as status, \
             patch.object(w, 'transition') as wake, patch.object(w, '_LAST_ACTIVITY', 0):
            thread = threading.Thread(target=w.serve_endpoint, kwargs={'port': server.server_port, 'once': True})
            thread.start()
            try:
                client.request(method, path, body=sql, headers=headers or {})
                result = client.getresponse()
                code, result_headers, data = result.status, dict(result.getheaders()), result.read()
            finally:
                client.close()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), 'once=True did not stop')
            return code, result_headers, json.loads(data), backend, wake, status, trino, w._LAST_ACTIVITY

    def test_endpoint_statement_wake_headers_and_continuation_urls(self):
        code, headers, page, backend, wake, _, _, activity = self.endpoint_request(
            'POST', '/v1/statement', 'SELECT 42; ',
            {'X-Trino-User': 'powerbi', 'X-Trino-Source': 'Power BI'}, replicas=0,
            payload={'nextUri': 'http://backend:8080/v1/statement/queued/q/1?x=2',
                     'partialCancelUri': 'http://backend:8080/v1/statement/queued/q/1', 'data': [[42]]})
        self.assertEqual(code, 200)
        wake.assert_called_once_with('wake', 'LOCAL')
        self.assertGreater(activity, 0)
        self.assertEqual(headers['X-Trino-Set-Catalog'], 'iceberg')
        self.assertEqual(page['data'], [[42]])
        self.assertTrue(page['nextUri'].startswith('http://127.0.0.1:'))
        self.assertTrue(page['nextUri'].endswith('/v1/statement/queued/q/1?x=2'))
        self.assertNotIn('backend', page['partialCancelUri'])
        call = backend.request.call_args
        self.assertEqual(call.args, ('POST', '/v1/statement'))
        self.assertEqual(call.kwargs['body'], b'SELECT 42')
        self.assertEqual(call.kwargs['headers']['X-Trino-Source'], 'Power BI')
        self.assertEqual(call.kwargs['headers']['X-Trino-User'], 'powerbi')
        backend.close.assert_called_once()

    def test_powerbi_desktop_import_simulation(self):
        headers = {
            'User-Agent': 'Microsoft.Data.Mashup (https://go.microsoft.com/fwlink/?LinkID=304249)',
            'X-Trino-User': 'bi_reader',
            'X-Trino-Catalog': 'iceberg',
            'X-Trino-Schema': 'marts',
        }
        cases = (
            ('SHOW SCHEMAS FROM iceberg',
             [{'name': 'Schema', 'type': 'varchar'}], [['marts']]),
            ('SHOW TABLES FROM iceberg.marts',
             [{'name': 'Table', 'type': 'varchar'}], [['fct_orders']]),
            ("SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'",
             [{'name': 'table_name', 'type': 'varchar'}], [['fct_orders']]),
            ('SELECT order_id, customer_code, total_amount, order_date FROM iceberg.marts.fct_orders',
             [{'name': 'order_id', 'type': 'bigint'},
              {'name': 'customer_code', 'type': 'varchar'},
              {'name': 'total_amount', 'type': 'decimal(18,2)'},
              {'name': 'order_date', 'type': 'date'}],
             [[1, '000123', '1250.50', '2026-10-17'],
              [2, '000007', '0.01', '2026-10-16']]),
        )
        for sql, columns, rows in cases:
            with self.subTest(sql=sql):
                payload = {'id': 'powerbi-import', 'columns': columns, 'data': rows,
                           'stats': {'state': 'FINISHED'}}
                code, _, page, backend, _, _, _, activity = self.endpoint_request(
                    'POST', '/v1/statement', sql, headers, payload=payload)
                self.assertEqual(code, 200)
                self.assertEqual(page, payload)
                backend.request.assert_called_once()
                call = backend.request.call_args
                self.assertEqual(call.args, ('POST', '/v1/statement'))
                self.assertEqual(call.kwargs['body'], sql.encode())
                for name, value in headers.items():
                    self.assertEqual(call.kwargs['headers'][name], value)
                self.assertGreater(activity, 0)
                backend.close.assert_called_once()

        for sql in ('SELECT * FROM iceberg.raw.orders', 'SELECT * FROM raw.orders'):
            with self.subTest(sql=sql):
                code, _, page, backend, wake, status, trino, activity = self.endpoint_request(
                    'POST', '/v1/statement', sql, headers, replicas=0)
                self.assertEqual(code, 403)
                self.assertIn('SQL policy violation', page['error']['message'])
                self.assertIn('restricted to iceberg.marts and metadata', page['error']['message'])
                for mock in (backend.request, wake, status, trino):
                    mock.assert_not_called()
                self.assertEqual(activity, 0)

    def test_endpoint_rejection_precedes_access(self):
        for sql, headers in (
                ('DELETE FROM iceberg.marts.t', {'X-Trino-User': 'bi_reader'}),
                ('SELECT * FROM "iceberg"."raw".t', {'X-Trino-User': 'BI_tool'}),
                ('SELECT * FROM staging.t', {'X-Trino-User': 'powerbi', 'X-Trino-Catalog': 'iceberg'}),
                ('SELECT * FROM t', {'X-Trino-User': 'bi_reader', 'X-Trino-Catalog': 'iceberg',
                                     'X-Trino-Schema': 'sandbox'})):
            with self.subTest(sql=sql):
                code, _, page, backend, wake, status, trino, activity = self.endpoint_request(
                    'POST', '/v1/statement', sql, headers, replicas=0)
                self.assertEqual(code, 403)
                self.assertIn('SQL policy violation', page['error']['message'])
                for mock in (backend.request, wake, status, trino):
                    mock.assert_not_called()
                self.assertEqual(activity, 0)

    def test_endpoint_continuations_info_errors_and_non_bi(self):
        for method, path in (('GET', '/v1/statement/queued/q/1?token=abc'),
                             ('DELETE', '/v1/statement/executing/q/1'),
                             ('POST', '/v1/statement/executing/q/1'), ('GET', '/v1/info')):
            code, _, _, backend, wake, status, _, _ = self.endpoint_request(method, path)
            self.assertEqual(code, 200)
            self.assertEqual(backend.request.call_args.args, (method, path))
            wake.assert_not_called()
            status.assert_not_called()
        code, _, page, _, _, _, _, activity = self.endpoint_request(
            'GET', '/v1/info', backend_status=503, payload={'error': {'message': 'unavailable'}})
        self.assertEqual(code, 503)
        self.assertEqual(page['error']['message'], 'unavailable')
        self.assertEqual(activity, 0)
        self.assertEqual(self.endpoint_request('GET', '/v1/info', failure=OSError('offline'))[0], 502)
        self.assertEqual(self.endpoint_request('GET', '/unrelated')[0], 404)
        code, _, _, _, wake, _, _, _ = self.endpoint_request(
            'POST', '/v1/statement', 'INSERT INTO iceberg.raw.t VALUES (1)', {'X-Trino-User': 'engineer'})
        self.assertEqual(code, 200)
        wake.assert_not_called()

    def test_endpoint_parser(self):
        for options, expected in (([], (8080, '127.0.0.1', 'LOCAL', False)),
                (['--port', '8090', '--bind', 'localhost', '--warehouse', 's', '--once'],
                 (8090, 'localhost', 'S', True))):
            with patch.object(w.sys, 'argv', ['warehouse', 'endpoint'] + options), \
                 patch.object(w, 'serve_endpoint', return_value={}) as serve, \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(w.main(), 0)
                serve.assert_called_once_with(*expected)

    def test_auto_chill_configuration_and_threshold(self):
        with patch.object(w, '_AUTO_CHILL_MINUTES', 0), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'monotonic', return_value=100) as clock, \
             patch.object(w, 'status', return_value={'replicas': 1}) as status, \
             patch.object(w, 'start_auto_chill_daemon') as start, \
             patch.object(w, 'stop_auto_chill_daemon') as stop, \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', return_value={'rows': [[0]]}), \
             patch.object(w, 'transition') as transition:
            self.assertFalse(w.check_auto_chill()['chilled'])
            status.assert_not_called()
            for minutes in (-1, 1441, True, 1.0, '1', None):
                with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                    w.configure_auto_chill(minutes)
            self.assertEqual(w.configure_auto_chill(1), {'auto_chill_minutes': 1, 'idle_seconds': 0})
            clock.return_value = 159
            self.assertFalse(w.check_auto_chill()['chilled'])
            clock.return_value = 160
            self.assertEqual(w.check_auto_chill(), dict(chilled=True, auto_chill_minutes=1, idle_seconds=60))
            transition.assert_called_once_with('chill', 'LOCAL')
            status.return_value = {'replicas': 0}
            self.assertFalse(w.check_auto_chill()['chilled'])
            transition.assert_called_once()
            self.assertEqual(w.configure_auto_chill(1440)['auto_chill_minutes'], 1440)
            self.assertEqual(w.configure_auto_chill(0)['auto_chill_minutes'], 0)
            self.assertEqual(start.call_count, 2)
            stop.assert_called_once_with()
            clock.return_value = 0
            self.assertEqual(w.auto_chill_status()['idle_seconds'], 0)

    def test_auto_chill_active_queries_postpone_chill(self):
        for count in (1, 7):
            with self.subTest(count=count), patch.object(w, '_AUTO_CHILL_MINUTES', 1), \
                 patch.object(w, '_LAST_ACTIVITY', 0), patch.object(w.time, 'monotonic', return_value=100), \
                 patch.object(w, 'status', return_value={'replicas': 1}), \
                 patch.object(w, 'trino', return_value=nullcontext('url')), \
                 patch.object(w, 'execute', return_value={'rows': [[count]]}) as execute, \
                 patch.object(w, 'transition') as transition:
                self.assertEqual(w.check_auto_chill(), dict(chilled=False, active_queries=count,
                    auto_chill_minutes=1, idle_seconds=0))
                self.assertEqual(w._LAST_ACTIVITY, 100)
                transition.assert_not_called()
                self.assertEqual(execute.call_args.args[1],
                    "SELECT COUNT(*) FROM system.runtime.queries WHERE state IN ('RUNNING', 'QUEUED') "
                    "AND query NOT LIKE '%system.runtime.queries%'")

    def test_auto_chill_query_failure_does_not_chill(self):
        with patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'monotonic', return_value=100), \
             patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', side_effect=RuntimeError('Trino unavailable')), \
             patch.object(w, 'transition') as transition:
            with self.assertRaisesRegex(RuntimeError, 'Trino unavailable'):
                w.check_auto_chill()
            transition.assert_not_called()

    def test_auto_chill_daemon_start_stop_and_restart(self):
        self.addCleanup(w.stop_auto_chill_daemon)
        with patch.object(w, '_AUTO_CHILL_MINUTES', 0), patch.object(w, '_LAST_ACTIVITY', 0):
            w.configure_auto_chill(1)
            first = w._AUTO_CHILL_THREAD
            self.assertTrue(first.daemon)
            self.assertTrue(first.is_alive())
            w.configure_auto_chill(2)
            self.assertIs(w._AUTO_CHILL_THREAD, first)
            w.configure_auto_chill(0)
            self.assertFalse(first.is_alive())
            self.assertIsNone(w._AUTO_CHILL_THREAD)
            w.stop_auto_chill_daemon()
            w.configure_auto_chill(1)
            self.assertIsNot(w._AUTO_CHILL_THREAD, first)
            w.configure_auto_chill(0)

    def test_auto_chill_daemon_checks_periodically_and_recovers(self):
        with patch.object(w, '_AUTO_CHILL_THREAD', None), \
             patch.object(w, '_AUTO_CHILL_STOP') as stop, \
             patch.object(w.threading, 'Thread') as thread, \
             patch.object(w, 'check_auto_chill', side_effect=[RuntimeError('temporary'), {}]) as check, \
             patch.object(w.logging, 'getLogger') as logger:
            stop.wait.side_effect = [False, False, True]
            w.start_auto_chill_daemon(interval=3)
            thread.return_value.start.assert_called_once_with()
            thread.call_args.kwargs['target']()
            self.assertEqual(check.call_count, 2)
            self.assertEqual(stop.wait.call_count, 3)
            stop.wait.assert_called_with(3)
            logger.return_value.exception.assert_called_once()
            w.stop_auto_chill_daemon()
            stop.set.assert_called_once_with()
            thread.return_value.join.assert_called_once_with()

    def test_activity_updated_only_for_successful_query_or_wake(self):
        with patch.object(w, '_LAST_ACTIVITY', 0), patch.object(w.time, 'monotonic', return_value=50), \
             patch.object(w, 'status', return_value={'replicas': 1, 'state': 'active'}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', return_value={'rows': []}) as execute, \
             patch.object(w, 'telemetry'):
            w.query('LOCAL', 'SELECT 1')
            self.assertEqual(w._LAST_ACTIVITY, 50)
            w._LAST_ACTIVITY = 0
            execute.side_effect = RuntimeError('failed')
            with self.assertRaises(RuntimeError):
                w.query('LOCAL', 'SELECT 1')
            self.assertEqual(w._LAST_ACTIVITY, 0)
            w.transition('wake', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 50)
            w._LAST_ACTIVITY = 0
            with patch.object(w, 'status', return_value={'replicas': 0, 'state': 'chilled'}):
                w.transition('chill', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 0)
            with patch.object(w, 'status', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    w.transition('wake', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 0)

    def test_qualify_rewrite(self):
        body = 'SELECT id, row_number() OVER (PARTITION BY id ORDER BY ts DESC) AS rn FROM events'
        for suffix in ('', ' ORDER BY id', ' LIMIT 5', ' ORDER BY id LIMIT 5'):
            with self.subTest(suffix=suffix):
                statement = body + ' QUALIFY rn = 1' + suffix
                expected = 'SELECT * FROM (' + body + ') AS _qualify_sub WHERE rn = 1' + suffix
                self.assertEqual(w.normalize_trino_sql(statement), expected)
                self.assertEqual(w.check_sql_policy(statement + '; '), expected)
        body = 'WITH ranked AS (' + body + ') SELECT * FROM ranked'
        self.assertEqual(w.normalize_trino_sql(body + ' qualify rn = 1'),
                         'SELECT * FROM (' + body + ') AS _qualify_sub WHERE rn = 1')

    def test_qualify_ignores_quotes_comments_and_nested_clauses(self):
        for statement in ("SELECT 'QUALIFY it''s ORDER BY LIMIT' AS s", 'SELECT "QUALIFY" FROM t',
                          'SELECT * FROM (SELECT * FROM t QUALIFY rn = 1)',
                          'WITH t AS (SELECT * FROM x QUALIFY rn = 1) SELECT * FROM t',
                          'SELECT 1 -- QUALIFY rn = 1\n', 'SELECT 1 /* QUALIFY rn = 1 */',
                          'CREATE TABLE t AS SELECT * FROM x QUALIFY rn = 1'):
            self.assertEqual(w.normalize_trino_sql(statement), statement)
        statement = "SELECT rn FROM t QUALIFY rn IN (SELECT rn FROM x ORDER BY rn LIMIT 1) AND 'LIMIT' <> '' ORDER BY rn LIMIT 3"
        self.assertEqual(w.normalize_trino_sql(statement),
                         "SELECT * FROM (SELECT rn FROM t) AS _qualify_sub WHERE rn IN (SELECT rn FROM x ORDER BY rn LIMIT 1) AND 'LIMIT' <> '' ORDER BY rn LIMIT 3")
        self.assertEqual(w.normalize_trino_sql('SELECT rn FROM t -- comment\nQUALIFY rn = 1'),
                         'SELECT * FROM (SELECT rn FROM t -- comment\n) AS _qualify_sub WHERE rn = 1')
        with self.assertRaisesRegex(ValueError, 'multiple statements'):
            w.check_sql_policy('SELECT rn FROM t QUALIFY rn = 1; SELECT 2')

    def test_sql_policy_read_only_and_quoting(self):
        for statement in ["SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x", "invalid SQL",
                          "SELECT 'DROP; it''s fine', \"DELETE;\"", "-- INSERT ignored\nSELECT 1"]:
            self.assertEqual(w.check_sql_policy(statement + ';  '), statement)
        for mode in ('read-only', 'sandbox-write', 'admin-write'):
            for statement in ["SELECT 1; SELECT 2", "SELECT 'x'; INSERT INTO iceberg.sandbox.t VALUES (1)"]:
                with self.subTest(mode=mode, statement=statement), self.assertRaisesRegex(ValueError, 'SQL policy violation'):
                    w.check_sql_policy(statement, mode)
            for verb in ('DROP TABLE', 'TRUNCATE TABLE', 'DELETE FROM', 'GRANT', 'REVOKE'):
                for prefix in ('', 'WITH x AS (SELECT 1) ', '/* comment */ '):
                    with self.subTest(mode=mode, verb=verb, prefix=prefix), self.assertRaises(ValueError):
                        w.check_sql_policy(prefix + verb.lower() + ' iceberg.sandbox.t', mode)
        with self.assertRaises(ValueError):
            w.check_sql_policy('SELECT 1', 'unknown')
        for verb in ('INSERT INTO', 'UPDATE', 'MERGE INTO', 'CREATE TABLE', 'ALTER TABLE', 'CALL'):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                w.check_sql_policy(verb + ' iceberg.sandbox.t')

    def test_sql_policy_write_targets(self):
        sandbox = ['CREATE SCHEMA IF NOT EXISTS iceberg.sandbox',
                   'CREATE TABLE iceberg.sandbox.any_table (id BIGINT)',
                   'CREATE OR REPLACE TABLE "iceberg"."sandbox"."other" AS SELECT 2',
                   'INSERT INTO iceberg.sandbox.any_table VALUES (1)',
                   'ALTER TABLE iceberg.sandbox.any_table EXECUTE rollback_to_snapshot(123)']
        admin = ['CREATE SCHEMA IF NOT EXISTS iceberg.other',
                 'CREATE OR REPLACE TABLE iceberg.marts.any_table AS SELECT 1',
                 'INSERT INTO iceberg.marts.any_table VALUES (1)',
                 'ALTER TABLE iceberg.other.any_table EXECUTE rollback_to_snapshot(123)']
        for statement in sandbox:
            for mode in ('sandbox-write', 'admin-write'):
                with self.subTest(statement=statement, mode=mode):
                    self.assertEqual(w.check_sql_policy(statement, mode), statement)
        for statement in admin:
            self.assertEqual(w.check_sql_policy(statement, 'admin-write'), statement)
            with self.assertRaises(ValueError):
                w.check_sql_policy(statement, 'sandbox-write')
        for mode in ('sandbox-write', 'admin-write'):
            for statement in ['CREATE TABLE marts.t AS SELECT 1', 'INSERT INTO iceberg.other.t VALUES (1)',
                              'CREATE TABLE iceberg.sandbox.t.extra AS SELECT 1',
                              'ALTER TABLE iceberg.sandbox.t RENAME TO iceberg.marts.t',
                              'ALTER TABLE iceberg.sandbox.t EXECUTE optimize()',
                              'UPDATE iceberg.sandbox.t SET x=1', 'CALL arbitrary()']:
                with self.subTest(mode=mode, statement=statement), self.assertRaises(ValueError):
                    w.check_sql_policy(statement, mode)

    def test_policy_rejection_precedes_warehouse_access(self):
        with patch.object(w, 'status') as status, patch.object(w, 'trino') as trino:
            with self.assertRaises(ValueError):
                w.query('LOCAL', 'INSERT INTO iceberg.marts.t VALUES (1)')
            status.assert_not_called()
            trino.assert_not_called()
        with patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', return_value={'rows': []}) as execute, patch.object(w, 'telemetry'):
            report = w.query('LOCAL', 'INSERT INTO iceberg.sandbox.t VALUES (1); ', mode='sandbox-write')
            self.assertEqual(report['mode'], 'sandbox-write')
            self.assertEqual(execute.call_args.args[1], 'INSERT INTO iceberg.sandbox.t VALUES (1)')

    def test_sizes_and_states(self):
        for size in ("xs", "s", "local"):
            self.assertEqual(w.warehouse(size), size.upper())
        with self.assertRaises(ValueError):
            w.warehouse("XL")
        for replicas, ready, state in [(0, 0, "chilled"), (1, 0, "starting"), (2, 1, "active")]:
            with patch.object(w, "deployment", return_value={
                "spec": {"replicas": replicas}, "status": {"readyReplicas": ready}
            }):
                self.assertEqual(w.status("xs"), dict(warehouse="XS", backend="trino-local", deployment="trino-local",
                    namespace="shirokuma-dev", state=state, replicas=replicas, ready_replicas=ready))

    @patch.object(w, "telemetry", return_value="skipped")
    def test_transitions_and_noops(self, telemetry):
        for action, replicas, state in [("chill", 1, "active"), ("wake", 0, "chilled")]:
            with patch.object(w, "status", return_value={"replicas": replicas, "state": state}), \
                 patch.object(w, "kubectl") as kube, \
                 patch.object(w, "deployment", side_effect=[
                     {"spec": {"replicas": 0}, "status": {"replicas": 1}},
                     {"spec": {"replicas": 0}, "status": {}}]), \
                 patch.object(w.time, "sleep"), patch.object(w, "trino", return_value=nullcontext()):
                report = w.transition(action, "XS")
                self.assertEqual(report["result"], "verified")
                self.assertEqual(report["backend"], "trino-local")
                kube.assert_any_call("scale", "deployment/trino-local", f"--replicas={1-replicas}")
                self.assertEqual(report["after_replicas"], 1-replicas)
        for action, replicas, state in [("chill", 0, "chilled"), ("wake", 1, "active")]:
            with patch.object(w, "status", return_value={"replicas": replicas, "state": state}), \
                 patch.object(w, "kubectl") as kube:
                self.assertEqual(w.transition(action, "S")["result"], "noop")
                kube.assert_not_called()

    @patch.object(w, "telemetry", return_value="recorded")
    def test_arbitrary_sql_pagination_and_auto_wake(self, telemetry):
        sql = "SELECT name FROM arbitrary_table WHERE amount > 17"
        with patch.object(w, "status", return_value={"replicas": 0}), \
             patch.object(w, "transition") as wake, \
             patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
             patch.object(w, "http_json", side_effect=[
                 {"id": "query1", "nextUri": "http://localhost:1234/next"},
                 {"columns": [{"name": "name"}], "data": [["a"], ["b"]]}]) as http:
            report = w.query("local", sql, "iceberg", "public")
            wake.assert_called_once_with("wake", "LOCAL")
            self.assertEqual(report["rows"], [["a"], ["b"]])
            self.assertEqual(report["row_count"], 2)
            self.assertEqual(report["backend"], "trino-local")
            self.assertFalse(report["truncated"])
            self.assertTrue(report["auto_woken"])
            self.assertEqual(report["query_id"], "query1")
            self.assertEqual(http.call_args_list[0].args[1], sql.encode())
            headers = http.call_args_list[0].args[2]
            self.assertEqual(headers["X-Trino-Catalog"], "iceberg")
            self.assertEqual(headers["X-Trino-Schema"], "public")
            self.assertEqual(headers["X-Trino-Source"], "shirokuma-warehouse-LOCAL")

    def test_query_rejections_and_trino_error(self):
        with self.assertRaises(ValueError):
            w.query("XS", "  ")
        with self.assertRaises(ValueError):
            w.query("LOCAL", "SELECT 1", max_rows=0)
        with patch.object(w, "status", return_value={"replicas": 0}), \
             patch.object(w, "transition") as wake:
            with self.assertRaises(ValueError):
                w.query("XS", "SELECT 1", no_auto_wake=True)
            wake.assert_not_called()
        for failure in ({"error": {"message": "bad SQL"}}, OSError("HTTP failed"), TimeoutError()):
            with patch.object(w, "status", return_value={"replicas": 1}), \
                 patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
                 patch.object(w, "http_json", side_effect=[failure]), patch.object(w, "telemetry") as telemetry:
                with self.assertRaises((RuntimeError, OSError)):
                    w.query("S", "invalid SQL")
                self.assertEqual(telemetry.call_args.args[0], "query")
                self.assertIsInstance(telemetry.call_args.args[1], int)
                self.assertEqual(telemetry.call_args.kwargs, {"status": "failed"})

    def test_default_local(self):
        for action in ("status", "chill", "wake", "query"):
            argv = ["warehouse", action] + (["--sql", "SELECT 1"] if action == "query" else [])
            with patch.object(w.sys, "argv", argv), patch.object(w, "status", return_value={}) as status, \
                 patch.object(w, "transition", return_value={}) as transition, \
                 patch.object(w, "query", return_value={}) as query, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(w.main(), 0)
                call = {"status": status, "query": query}.get(action, transition).call_args
                self.assertEqual(call.args[1 if action in ("chill", "wake") else 0], "LOCAL")
                if action == "query":
                    self.assertEqual(call.args[-1], 1000)

    def test_query_mode_parser(self):
        with patch.object(w.sys, 'argv', ['warehouse', 'query', '--sql', 'SELECT 1', '--mode', 'admin-write']), \
             patch.object(w, 'query', return_value={}) as query, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(w.main(), 0)
        self.assertEqual(query.call_args.kwargs, {'mode': 'admin-write'})
        self.assertEqual(query.call_args.args[-1], 1000)

    @patch.object(w, "telemetry", return_value="skipped")
    def test_bounded_rows_and_best_effort_cancel(self, telemetry):
        for count, next_uri, cancel_error in [(3, "http://localhost/next", False),
                                             (2, "http://localhost/next", True), (3, None, False), (2, None, False)]:
            with patch.object(w, "status", return_value={"replicas": 1}), \
                 patch.object(w, "trino", return_value=nullcontext("http://localhost")), \
                 patch.object(w, "http_json", return_value={"data": [[i] for i in range(count)],
                              "nextUri": next_uri}) as http, \
                 patch.object(w.urllib.request, "urlopen", side_effect=OSError() if cancel_error else None) as cancel:
                report = w.query("LOCAL", "SELECT arbitrary", max_rows=2)
                self.assertEqual(report["rows"], [[0], [1]])
                self.assertEqual(report["truncated"], count > 2 or bool(next_uri))
                http.assert_called_once()
                self.assertEqual(cancel.call_count, int(bool(next_uri)))
                if next_uri:
                    self.assertEqual(cancel.call_args.args[0].get_method(), "DELETE")
                    self.assertEqual(cancel.call_args.args[0].full_url, next_uri)

    def test_port_forward_cleanup_on_failure_and_success(self):
        for failure in (False, True):
            process = Mock()
            process.poll.return_value = None
            with patch.object(w.subprocess, "Popen", return_value=process) as popen, \
                 patch.object(w.socket, "socket") as socket, \
                 patch.object(w, "http_json", return_value={"starting": False}):
                socket.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 1234)
                try:
                    with w.trino() as url:
                        self.assertTrue(url.startswith("http://127.0.0.1:"))
                        if failure:
                            raise RuntimeError("query failed")
                except RuntimeError:
                    pass
                process.terminate.assert_called_once()
                process.wait.assert_called_once()
                self.assertIn("svc/trino-local", popen.call_args.args[0])

    def test_uninitialized_telemetry_is_best_effort(self):
        with patch.dict("sys.modules", {"agenthouse.manage": None}):
            self.assertEqual(w.telemetry("query", 12), "skipped")

    def test_persistent_trino_reuses_and_owns_tunnel(self):
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(w.subprocess, 'Popen') as popen, \
                 patch.object(w, 'http_json', return_value={'starting': False}):
                process = popen.return_value
                process.poll.return_value = None
                with self.assertRaisesRegex(RuntimeError, 'caller failed') if failure else nullcontext():
                    with w.persistent_trino() as persistent_url:
                        for _ in range(2):
                            with w.trino() as url:
                                self.assertEqual(url, persistent_url)
                        with w.persistent_trino() as nested_url:
                            self.assertEqual(nested_url, persistent_url)
                        process.terminate.assert_not_called()
                        popen.assert_called_once()
                        if failure:
                            raise RuntimeError('caller failed')
                self.assertIsNone(w._PERSISTENT_TRINO)
                process.terminate.assert_called_once()
                process.wait.assert_called_once()

    def test_dead_persistent_tunnel_falls_back_and_failed_start_cleans_up(self):
        dead = Mock()
        dead.poll.return_value = 1
        with patch.object(w, '_PERSISTENT_TRINO', {'process': dead, 'url': 'dead'}), \
             patch.object(w.subprocess, 'Popen') as popen, \
             patch.object(w, 'http_json', return_value={'starting': False}):
            popen.return_value.poll.return_value = None
            with w.trino() as url:
                self.assertNotEqual(url, 'dead')
            popen.return_value.terminate.assert_called_once()
            dead.terminate.assert_not_called()
        with patch.object(w.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, 'port-forward exited'):
                with w.persistent_trino():
                    self.fail('failed startup must not yield')
            self.assertIsNone(w._PERSISTENT_TRINO)
            popen.return_value.terminate.assert_called_once()

    def test_rich_telemetry_omits_none_and_passes_event_fields(self):
        manage = Mock()
        with patch.dict('sys.modules', {'agenthouse.manage': manage}):
            self.assertEqual(w.telemetry('bear.data_engineer', 7, prefix='', target='iceberg.marts.t@42',
                risk_level='normal', model_name='local-model', prompt_tokens=0,
                completion_tokens=None, blocked_by_policy=False), 'recorded')
        fields = manage.event.call_args.kwargs
        self.assertEqual(fields['target'], 'iceberg.marts.t@42')
        self.assertEqual(fields['action'], 'bear.data_engineer')
        self.assertEqual(fields['risk_level'], 'normal')
        self.assertEqual(fields['prompt_tokens'], 0)
        self.assertFalse(fields['blocked_by_policy'])
        self.assertNotIn('completion_tokens', fields)

    def test_failed_pawprint_has_only_action_and_timing(self):
        manage = Mock()
        with patch.dict("sys.modules", {"agenthouse.manage": manage}):
            self.assertEqual(w.telemetry("query", 12, status="failed"), "recorded")
        fields = manage.event.call_args.kwargs
        self.assertEqual(fields["action"], "warehouse.query")
        self.assertEqual(fields["status"], "failed")
        self.assertEqual(set(fields), {"action", "target", "status", "agent_id", "task_id", "trace_id", "duration_ms"})


if __name__ == "__main__":
    unittest.main()
