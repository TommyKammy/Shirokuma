import contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace as Args
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "local_experiment"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("bears_resident", TOOLS / "bears_resident.py")
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


class BearsResidentTests(unittest.TestCase):
    def test_clone_targets_snapshots_and_audit(self):
        for target, full in [(None, 'iceberg.sandbox.orders_clone'), ('', 'iceberg.sandbox.orders_clone'),
                             ('custom', 'iceberg.sandbox.custom'), ('raw.copy', 'iceberg.raw.copy'),
                             ('iceberg.sandbox.copy', 'iceberg.sandbox.copy')]:
            for snapshot in (None, '', '00012', 9007199254740993):
                with self.subTest(target=target, snapshot=snapshot), \
                     patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
                     patch.object(b, 'sql') as sql, \
                     patch.object(b, 'audit_table', return_value={'snapshot_id': 123}) as audit, \
                     patch.object(b.wr, 'telemetry', return_value='recorded') as telemetry:
                    result = b.clone_table('marts.orders', target, snapshot)
                    self.assertEqual(result, dict(action='clone', source_table='iceberg.marts.orders',
                        table=full, snapshot_id=str(snapshot) if snapshot not in (None, '') else None,
                        audit={'snapshot_id': 123}, telemetry='recorded'))
                    statement = 'CREATE OR REPLACE TABLE ' + b.identifier(full) + ' AS SELECT * FROM "iceberg"."marts"."orders"'
                    if snapshot not in (None, ''):
                        statement += f' FOR VERSION AS OF {int(snapshot)}'
                    self.assertEqual(sql.call_args_list[0].args, ('url', 'CREATE SCHEMA IF NOT EXISTS iceberg.' + full.split('.')[1]))
                    sql.assert_called_with('url', statement)
                    audit.assert_called_once_with('url', full)
                    self.assertEqual(telemetry.call_args.args[::2], ('catalog.clone', 'success'))
                    self.assertEqual(telemetry.call_args.kwargs, dict(prefix='', target=full))

    def test_clone_validation_and_failure_telemetry(self):
        with patch.object(b.wr, 'trino') as trino:
            for source in ('orders', 'other.raw.t', 'raw.bad-name', 'raw.t; DROP', None):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    b.clone_table(source)
            for target in ('marts.x', 'tpch_tiny.x', 'other.raw.x', 'a.b.c.d', 'bad-name', 1):
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'clone target must be in sandbox or raw schema'):
                    b.clone_table('iceberg.raw.orders', target)
            for snapshot in ('-1', '1.0', '１２', '1;DROP', True):
                with self.subTest(snapshot=snapshot), self.assertRaisesRegex(ValueError, 'snapshot_id must be digits-only'):
                    b.clone_table('raw.orders', snapshot_id=snapshot)
            trino.assert_not_called()
        for failed in ('sql', 'audit_table'):
            with patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
                 patch.object(b, 'sql'), patch.object(b, 'audit_table'), \
                 patch.object(b, failed, side_effect=RuntimeError('failed')), \
                 patch.object(b.wr, 'telemetry') as telemetry:
                with self.assertRaises(RuntimeError):
                    b.clone_table('raw.orders')
                self.assertEqual(telemetry.call_args.args[2], 'failed')

    def test_activity_history_filters_and_independent_failures(self):
        actions = ['warehouse.query', 'lake.ingest', 'lake.export', 'catalog.clone', 'catalog.drop',
                   'bear.data_engineer', 'warehouse.wake', 'bear.sre']
        events = [dict(action_type=a) for a in actions] + [{}]
        with patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
             patch.object(b, 'sql', return_value={'rows': [['q1']]}) as sql, \
             patch('agenthouse.manage.Client') as client:
            client.return_value.query.return_value = json.dumps({'data': events})
            result = b.activity_history(7)
            self.assertEqual(result['runtime_queries'], {'rows': [['q1']]})
            self.assertEqual(result['query_history'], events[:1])
            self.assertEqual(result['load_history'], events[1:6])
            sql.assert_called_once_with('url', 'SELECT query_id, state, "user", source, query FROM system.runtime.queries '
                                        "WHERE source <> 'shirokuma-bears' ORDER BY created DESC LIMIT 7")
            client.return_value.query.assert_called_once_with(
                'SELECT * FROM agenthouse.pawprints ORDER BY timestamp DESC LIMIT 100 FORMAT JSON')
            sql.side_effect = RuntimeError('runtime unavailable')
            self.assertEqual(b.activity_history()['load_history'], events[1:6])
            sql.side_effect = None
            client.return_value.query.side_effect = RuntimeError('history unavailable')
            result = b.activity_history()
            self.assertIn('error', result['query_history'])
            self.assertEqual(result['runtime_queries'], {'rows': [['q1']]})
        with patch.object(b.wr, 'trino', side_effect=RuntimeError('tunnel unavailable')), \
             patch('agenthouse.manage.Client', side_effect=RuntimeError('client unavailable')):
            result = b.activity_history()
            self.assertIn('error', result['runtime_queries'])
            self.assertIn('error', result['load_history'])
        for limit in (True, 0, -1, '1'):
            with self.assertRaises(ValueError):
                b.activity_history(limit)

    def test_sql_honors_export_limits_without_changing_default_calls(self):
        with patch.object(b.wr, 'execute', return_value={'rows': []}) as execute:
            b.sql('url', 'SELECT * FROM "iceberg"."raw"."orders" LIMIT 10000')
            self.assertEqual(execute.call_args.kwargs, {'max_rows': 10000})
            b.sql('url', 'SELECT 1')
            self.assertEqual(len(execute.call_args.args), 3)
            self.assertEqual(execute.call_args.kwargs, {})

    def test_snapshot_preview_and_validation(self):
        preview = {'rows': [[1, 'old']]}
        for current in ({'rows': [[5]]}, RuntimeError('current missing')):
            with patch.object(b, 'sql', side_effect=[preview, {'rows': [[2]]}, current]) as sql:
                report = b.catalog(Args(action='search', table='raw.orders', snapshot_id='9007199254740993'))
            self.assertEqual(report, dict(action='snapshot_preview', catalog='iceberg', table='iceberg.raw.orders',
                snapshot_id='9007199254740993', row_count=2,
                current_row_count=None if isinstance(current, Exception) else 5, preview=preview))
            self.assertEqual(sql.call_args_list[0].args[1],
                'SELECT * FROM "iceberg"."raw"."orders" FOR VERSION AS OF 9007199254740993 LIMIT 50')
            self.assertIn('FOR VERSION AS OF 9007199254740993', sql.call_args_list[1].args[1])
        for value in ('1; DROP', '-1', '1.0', '', '１２'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'snapshot_id must be digits-only'):
                b.catalog(Args(action='describe', table='orders', snapshot_id=value))
        with self.assertRaisesRegex(ValueError, 'table is required'):
            b.catalog(Args(action='describe', table=None, snapshot_id='1'))

    def test_full_catalog_detail_and_best_effort_failures(self):
        for failed in (False, True):
            def execute(url, statement):
                if failed and statement.startswith(('SELECT *', 'SHOW CREATE')):
                    raise RuntimeError('unavailable')
                return {'rows': [['value']]}
            with patch.object(b, 'sql', side_effect=execute), \
                 patch.object(b, 'audit_table', side_effect=RuntimeError('audit unavailable') if failed else None,
                              return_value={'row_count': 3}), \
                 patch.object(b, '_table_lineage', return_value={'upstream': [], 'downstream': []}):
                metadata = b.catalog(Args(action='describe', table='orders', detail='full', include_config=False))['metadata']
            self.assertEqual(set(metadata), {'columns', 'snapshots', 'preview', 'profile', 'definition', 'lineage_detail'})
            if failed:
                for field in ('preview', 'profile', 'definition'):
                    self.assertIn('error', metadata[field])
            else:
                self.assertEqual(metadata['profile']['row_count'], 3)
                self.assertEqual(metadata['definition'], [['value']])

    def test_catalog_sections_execute_only_requested_query(self):
        quoted = '"iceberg"."custom"."orders"'
        cases = [
            ('columns', 'columns', 'SHOW COLUMNS FROM ' + quoted, True),
            ('preview', 'preview', 'SELECT * FROM ' + quoted + ' LIMIT 50', False),
            ('ddl', 'definition', 'SHOW CREATE TABLE ' + quoted, True),
            ('snapshots', 'snapshots', 'SELECT snapshot_id, parent_id, committed_at FROM '
             '"iceberg"."custom"."orders$snapshots" ORDER BY committed_at DESC', False),
        ]
        response = {'rows': [['id', 'bigint', '', 'identifier']], 'query_id': 'query-1'}
        for detail, key, statement, rows_only in cases:
            # Even the CLI default include_config=True must not add other sections.
            with self.subTest(detail=detail), patch.object(b, 'sql', return_value=response) as sql, \
                 patch.object(b, 'audit_table') as audit, patch.object(b, '_table_lineage') as lineage, \
                 patch.object(b.wr, 'kubectl') as kubectl:
                result = b.catalog(Args(action='describe', table='custom.orders', detail=detail))
            self.assertEqual(result, dict(action='describe', catalog='iceberg',
                metadata={key: response['rows'] if rows_only else response}))
            sql.assert_called_once_with('http://trino', statement)
            audit.assert_not_called()
            lineage.assert_not_called()
            kubectl.assert_not_called()

    def test_catalog_snapshot_preview_keeps_precedence_over_valid_detail(self):
        snapshot = '9007199254740993'
        for detail in ('full', 'columns', 'preview', 'profile', 'ddl', 'snapshots', 'lineage'):
            with self.subTest(detail=detail), patch.object(b, 'sql', side_effect=[
                    {'rows': [[1]]}, {'rows': [[2]]}, {'rows': [[3]]}]) as sql, \
                 patch.object(b, 'audit_table') as audit, patch.object(b, '_table_lineage') as lineage:
                report = b.catalog(Args(action='describe', table='marts.orders', detail=detail, snapshot_id=snapshot))
            self.assertEqual(report, dict(action='snapshot_preview', catalog='iceberg', table='iceberg.marts.orders',
                snapshot_id=snapshot, row_count=2, current_row_count=3, preview={'rows': [[1]]}))
            table = '"iceberg"."marts"."orders"'
            self.assertEqual([call.args[1] for call in sql.call_args_list], [
                f'SELECT * FROM {table} FOR VERSION AS OF {snapshot} LIMIT 50',
                f'SELECT COUNT(*) FROM {table} FOR VERSION AS OF {snapshot}', f'SELECT COUNT(*) FROM {table}'])
            audit.assert_not_called()
            lineage.assert_not_called()

    def test_catalog_section_failures_stay_with_requested_section(self):
        for detail, key in [('preview', 'preview'), ('profile', 'profile'), ('ddl', 'definition'),
                            ('snapshots', 'snapshots')]:
            with self.subTest(detail=detail), patch.object(b, 'sql', side_effect=RuntimeError('unavailable')) as sql:
                metadata = b.catalog(Args(action='describe', table='orders', detail=detail))['metadata']
            self.assertEqual(metadata, {key: {'error': 'unavailable'}})
            self.assertEqual(sql.call_count, 1)

    def test_catalog_lineage_uses_only_local_metadata(self):
        registry = {'orders': dict(table='iceberg.marts.orders', sources=['raw.orders'], sql='SELECT 1'),
                    'summary': dict(table='iceberg.marts.summary', sources=['marts.orders'])}
        with patch.object(b, 'load_wap_registry', return_value=registry), \
             patch.object(b.wr, 'trino') as trino, patch.object(b, 'sql') as sql, \
             patch.object(b.wr, 'kubectl') as kubectl:
            result = b.catalog(Args(action='describe', table='orders', detail='lineage'))
        self.assertEqual(result['metadata'], {'lineage_detail': dict(upstream=['raw.orders'],
            downstream=['iceberg.marts.summary'], layer='wap', wap_sql='SELECT 1')})
        trino.assert_not_called()
        sql.assert_not_called()
        kubectl.assert_not_called()

    def test_catalog_invalid_detail_fails_before_queries(self):
        with patch.object(b.wr, 'trino') as trino, patch.object(b, 'sql') as sql, \
             patch.object(b, '_table_lineage') as lineage:
            for detail in ('', 'unknown', 'FULL', 0, [], {}):
                for snapshot in (None, '9007199254740993'):
                    with self.subTest(detail=detail, snapshot=snapshot), \
                         self.assertRaisesRegex(ValueError, 'invalid catalog detail'):
                        b.catalog(Args(action='describe', table='marts.orders', detail=detail, snapshot_id=snapshot))
            for detail in ('columns', 'preview', 'profile', 'ddl', 'snapshots', 'lineage', 'full'):
                with self.subTest(detail=detail), self.assertRaisesRegex(ValueError, 'table is required for detail'):
                    b.catalog(Args(action='search', table=None, detail=detail))
        trino.assert_not_called()
        sql.assert_not_called()
        lineage.assert_not_called()

    def test_catalog_access_applies_to_every_section(self):
        with patch.object(b.wr, 'trino') as trino, patch.object(b, 'sql') as sql, \
             patch.object(b, '_table_lineage') as lineage:
            for detail in (None, 'full', 'columns', 'preview', 'profile', 'ddl', 'snapshots', 'lineage'):
                with self.subTest(detail=detail), self.assertRaises(PermissionError):
                    b.catalog(Args(action='describe', table='raw.orders', detail=detail, role='bi_viewer'))
        trino.assert_not_called()
        sql.assert_not_called()
        lineage.assert_not_called()

    def test_catalog_full_reuses_columns_and_preserves_profile(self):
        table = '"iceberg"."marts"."orders"'
        columns = [['order id', 'bigint', '', 'key'], ['amount', 'decimal(12,2)'], ['a"b', 'varchar']]
        active, newest = 9007199254740993, 9223372036854775806
        show_columns = 'SHOW COLUMNS FROM ' + table
        aggregates = ('SELECT COUNT(*), COUNT(*) - COUNT("order id"), COUNT(*) - COUNT("amount"), '
                      'COUNT(*) - COUNT("a""b") FROM ' + table)
        sample = 'SELECT * FROM ' + table + ' LIMIT 5'
        history = 'SELECT snapshot_id FROM "iceberg"."marts"."orders$history" ORDER BY made_current_at DESC LIMIT 1'
        snapshots = ('SELECT snapshot_id, parent_id, committed_at FROM "iceberg"."marts"."orders$snapshots" '
                     'ORDER BY committed_at DESC')
        preview = 'SELECT * FROM ' + table + ' LIMIT 50'
        ddl = 'SHOW CREATE TABLE ' + table
        replies = {show_columns: {'rows': columns}, aggregates: {'rows': [[7, 0, 2, 1]]},
                   sample: {'rows': [[1, '2.50', None]]}, history: {'rows': [[active]]},
                   snapshots: {'rows': [[newest, active, 'newest'], [active, None, 'active']]},
                   preview: {'rows': [[1, '2.50', None]]}, ddl: {'rows': [['CREATE TABLE orders (...)']]}}
        profiles = []
        for detail in ('full', 'profile'):
            with self.subTest(detail=detail), patch.object(b, 'sql', side_effect=lambda url, sql: replies[sql]) as sql, \
                 patch.object(b, '_table_lineage', return_value={'upstream': [], 'downstream': []}), \
                 patch.object(b.wr, 'kubectl') as kubectl:
                metadata = b.catalog(Args(action='describe', table='orders', detail=detail,
                                          include_config=False))['metadata']
            profiles.append(metadata['profile'])
            expected = ([show_columns, snapshots, preview, aggregates, sample, history, ddl]
                        if detail == 'full' else [show_columns, aggregates, sample, history])
            self.assertEqual([call.args[1] for call in sql.call_args_list], expected)
            self.assertEqual(set(metadata), {'columns', 'snapshots', 'preview', 'profile', 'definition', 'lineage_detail'}
                             if detail == 'full' else {'profile'})
            kubectl.assert_not_called()
        self.assertEqual(profiles[0], profiles[1])
        self.assertEqual(profiles[0], dict(table='iceberg.marts.orders', row_count=7, column_count=3,
            columns=[dict(name=row[0], type=row[1]) for row in columns],
            null_counts={'order id': 0, 'amount': 2, 'a"b': 1}, sample_rows=[[1, '2.50', None]],
            snapshot_id=active, quality_passed=True))

    def test_catalog_omitted_detail_preserves_existing_response(self):
        for include_config in (False, True):
            with self.subTest(include_config=include_config), \
                 patch.object(b, 'sql', return_value={'rows': [['metadata']]}) as sql, \
                 patch.object(b, 'audit_table') as audit, patch.object(b, '_table_lineage') as lineage, \
                 patch.object(b.wr, 'kubectl', return_value='config') as kubectl:
                result = b.catalog(Args(action='describe', table='orders', include_config=include_config))
            expected = {'columns': [['metadata']], 'snapshots': {'rows': [['metadata']]}}
            statements = ['SHOW COLUMNS FROM "iceberg"."marts"."orders"']
            if include_config:
                expected.update(definition=[['metadata']], catalog_config='config')
                statements.append('SHOW CREATE TABLE "iceberg"."marts"."orders"')
            statements.append('SELECT snapshot_id, parent_id, committed_at FROM '
                              '"iceberg"."marts"."orders$snapshots" ORDER BY committed_at DESC')
            self.assertEqual(result, dict(action='describe', catalog='iceberg', metadata=expected))
            self.assertEqual([call.args[1] for call in sql.call_args_list], statements)
            self.assertEqual(kubectl.call_count, int(include_config))
            audit.assert_not_called()
            lineage.assert_not_called()

    def test_catalog_http_sections_preserve_authenticated_role_and_large_ids(self):
        from email.message import Message
        import workbench_server as server
        active = 9007199254740993
        original_role = server.request_auth.current_role()

        def execute(url, statement, headers):
            self.assertEqual(server.request_auth.current_role(), 'bi_viewer')
            if statement.startswith('SHOW COLUMNS'):
                return {'rows': [['id', 'bigint']]}
            if '$snapshots' in statement:
                return {'rows': [[active, active - 1, 'timestamp']]}
            if '$history' in statement:
                return {'rows': [[active]]}
            if statement.startswith('SELECT COUNT'):
                return {'rows': [[2, 0]]}
            return {'rows': [[1]]}

        for detail, key in [('columns', 'columns'), ('preview', 'preview'), ('profile', 'profile'),
                            ('ddl', 'definition'), ('snapshots', 'snapshots'), ('lineage', 'lineage_detail')]:
            handler = object.__new__(server.WorkbenchHandler)
            handler.path = '/api/catalog?table=marts.orders&detail=' + detail
            handler.headers = Message()
            with self.subTest(detail=detail), patch.object(server, 'bears_resident', b), \
                 patch.object(server.request_auth, 'authenticate', return_value=('reader', 'bi_viewer')), \
                 patch.object(b.wr, 'execute', side_effect=execute) as sql, \
                 patch.object(b, '_table_lineage', return_value={'upstream': [], 'downstream': []}), \
                 patch.object(handler, 'respond') as respond:
                handler.do_GET()
            self.assertEqual(handler.authenticated_user, 'reader')
            metadata = respond.call_args.args[0]['metadata']
            self.assertEqual(set(metadata), {key})
            if detail == 'snapshots':
                self.assertEqual(metadata[key]['rows'][0], [str(active), str(active - 1), 'timestamp'])
            elif detail == 'profile':
                self.assertEqual(metadata[key]['snapshot_id'], str(active))
            elif detail == 'lineage':
                sql.assert_not_called()
            self.assertEqual(server.request_auth.current_role(), original_role)

    def test_catalog_http_rejects_invalid_detail_before_backend(self):
        from email.message import Message
        import workbench_server as server
        for query in ('table=marts.orders&detail=', 'table=marts.orders&detail=bad',
                      'detail=columns', 'detail=full'):
            handler = object.__new__(server.WorkbenchHandler)
            handler.path = '/api/catalog?' + query
            handler.headers = Message()
            with self.subTest(query=query), patch.object(server, 'bears_resident', b), \
                 patch.object(server.request_auth, 'authenticate', return_value=('reader', 'bi_viewer')), \
                 patch.object(b, 'catalog') as catalog, patch.object(handler, 'respond') as respond:
                handler.do_GET()
            self.assertEqual(respond.call_args.args[1], 400)
            catalog.assert_not_called()

    def test_drop_restrictions_and_telemetry(self):
        for schema in ('raw', 'sandbox', 'staging', 'marts', 'scratch'):
            with patch.object(b, 'sql') as sql, patch.object(b.wr, 'telemetry', return_value='recorded') as telemetry:
                report = b.drop_table(schema + '.orders')
            self.assertEqual(report, dict(action='drop', table=f'iceberg.{schema}.orders', dropped=True, telemetry='recorded'))
            self.assertEqual(sql.call_args.args[1], f'DROP TABLE IF EXISTS "iceberg"."{schema}"."orders"')
            self.assertEqual(telemetry.call_args.args[0], 'catalog.drop')
            self.assertEqual(telemetry.call_args.args[2], 'success')
            self.assertEqual(telemetry.call_args.kwargs['target'], report['table'])
        for table in ('tpch_tiny.x', 'smoke.x', 'raw.bad-name', 'raw.x; DROP', 'x', None, 'other.raw.x'):
            with patch.object(b, 'sql') as sql, self.assertRaisesRegex(ValueError, 'drop is only allowed'):
                b.drop_table(table)
            sql.assert_not_called()
        with patch.object(b, 'sql', side_effect=RuntimeError('failed')), patch.object(b.wr, 'telemetry') as telemetry:
            with self.assertRaises(RuntimeError):
                b.drop_table('iceberg.raw.x')
            self.assertEqual(telemetry.call_args.args[2], 'failed')

    def test_models_inventory_and_table_lineage(self):
        registry = {'derived': dict(table='iceberg.marts.derived', sql='SELECT * FROM marts.fct_orders',
                                    sources=['marts.fct_orders']),
                    'dependent': dict(staged_table='iceberg.sandbox.dependent', sql='SELECT * FROM marts.derived',
                                      sources=['iceberg.marts.derived'])}
        with patch.object(b, 'load_wap_registry', return_value=registry):
            inventory = b.models_inventory()
            models = {model['name']: model for model in inventory['dbt_models']}
            self.assertEqual(models['stg_tpch_orders']['layer'], 'staging')
            self.assertEqual(models['stg_tpch_orders']['sources'], ['tpch_tiny.orders'])
            self.assertIn('iceberg.marts.fct_orders', models['stg_tpch_orders']['downstream'])
            self.assertIn('iceberg.marts.derived', models['fct_orders']['downstream'])
            self.assertIn('select', models['fct_orders']['sql'])
            self.assertEqual(inventory['lineage']['derived'], ['marts.fct_orders'])
            self.assertEqual(b._table_lineage('iceberg.marts.derived'), dict(
                upstream=['marts.fct_orders'], downstream=['iceberg.sandbox.dependent'],
                layer='wap', wap_sql=registry['derived']['sql']))
            self.assertEqual(b._table_lineage('iceberg.raw.unknown'), dict(upstream=[], downstream=[]))

    def test_extract_source_tables(self):
        cases = [
            ('SELECT * FROM iceberg.raw.syn_orders JOIN raw.customers USING (id) JOIN products ON true',
             ['products', 'raw.customers', 'raw.syn_orders']),
            ('WITH RECURSIVE orders AS (SELECT * FROM iceberg.raw.orders), '
             'totals AS (SELECT * FROM orders JOIN raw.items ON true) '
             'SELECT * FROM totals JOIN orders ON true JOIN raw.orders ON true',
             ['raw.items', 'raw.orders']),
            ('WITH "my cte" AS (SELECT * FROM "iceberg" . "raw" . "order items") '
             'SELECT * FROM "my cte" JOIN "local table" ON true', ['local table', 'raw.order items']),
            ("SELECT 'FROM fake JOIN bogus', 'it''s FROM missing' FROM raw.real "
             '-- JOIN ignored\n/* FROM ignored.too */ JOIN raw.other ON true', ['raw.other', 'raw.real']),
            ('SELECT "FROM fake" FROM (SELECT * FROM RAW.Orders) q '
             'JOIN raw.orders ON true JOIN UNNEST(ARRAY[1]) ON true', ['raw.orders']),
            ('SELECT 1', []),
        ]
        for statement, expected in cases:
            with self.subTest(statement=statement):
                self.assertEqual(b.extract_source_tables(statement), expected)

    def test_registry_fail_closed_and_merge(self):
        self.assertEqual(b.load_wap_registry(), {})
        b.save_wap_entry('orders', sql='SELECT 1', sources=['raw.orders'])
        b.save_wap_entry('another', sql='SELECT 2')
        entry = b.save_wap_entry('orders', published=True)
        self.assertEqual(entry['sql'], 'SELECT 1')
        self.assertTrue(b.load_wap_registry()['orders']['published'])
        self.assertIn('another', b.load_wap_registry())
        for invalid in ('{', '[]', '{"bad": 1}'):
            b.WAP_REGISTRY.write_text(invalid)
            self.assertEqual(b.load_wap_registry(), {})
        with patch.object(Path, 'read_text', side_effect=OSError('unreadable')), \
             patch.object(Path, 'write_text', side_effect=OSError('unwritable')):
            with self.assertRaisesRegex(RuntimeError, 'failed to persist WAP registry: unwritable'):
                b.save_wap_entry('orders', sql='SELECT 1')

    def test_stage_publish_and_catalog_wap_lineage(self):
        statement = 'SELECT * FROM iceberg.raw.orders JOIN raw.items USING (id)'
        with patch.object(b, 'sql', return_value={'rows': []}), \
             patch.object(b, 'audit_table', return_value={'quality_passed': True, 'snapshot_id': 12}):
            self.assertTrue(b.data(Args(sql=statement, request='order items', stage_table='dynamic_orders'))['valid'])
        with patch.object(b, 'sql', return_value={'rows': []}), \
             patch.object(b, 'latest_snapshot', return_value=12), \
             patch.object(b, 'audit_table', side_effect=[dict(row_count=10, null_counts={}), dict(snapshot_id=34)]):
            self.assertTrue(b.data(Args(publish='dynamic_orders'))['valid'])
        entry = b.load_wap_registry()['dynamic_orders']
        self.assertEqual(entry['sql'], statement)
        self.assertEqual(entry['intent'], 'order items')
        self.assertEqual(entry['sources'], ['raw.items', 'raw.orders'])
        self.assertEqual(entry['staged_snapshot_id'], 12)
        self.assertEqual(entry['published_snapshot_id'], 34)
        self.assertTrue(entry['published'])
        lineage = b.catalog(Args(action='lineage', table=None))['metadata']['lineage']
        self.assertEqual(lineage['dynamic_orders'], entry['sources'])
        self.assertIn('stg_tpch_orders', lineage['fct_orders'])
        b.save_wap_entry('fct_orders', sources=['raw.unrelated'])
        self.assertEqual(b.catalog(Args(action='lineage', table=None))['metadata']['lineage']['fct_orders'],
                         lineage['fct_orders'])
        with patch.object(b, 'sql', return_value={'rows': []}), patch.object(b.wr, 'kubectl', return_value='{}'):
            metadata = b.catalog(Args(action='lineage', table='dynamic_orders'))['metadata']
            self.assertEqual(metadata['lineage'], entry['sources'])
            self.assertEqual(metadata['wap_sql'], statement)

    def test_refresh_saved_sql_and_override(self):
        for override in (None, 'SELECT * FROM raw.updated'):
            saved = 'SELECT * FROM raw.orders'
            b.save_wap_entry('dynamic_orders', sql=saved, intent='count orders')
            def sql(_url, statement):
                return {'rows': [['dynamic_orders']] if 'information_schema.tables' in statement else
                        [[10]] if statement.startswith('SELECT COUNT') else []}
            with patch.object(b, 'sql', side_effect=sql) as execute, \
                 patch.object(b, 'latest_snapshot', side_effect=[23, 12]), \
                 patch.object(b, 'audit_table', side_effect=[dict(row_count=10, null_counts={}, snapshot_id=23),
                                                           dict(snapshot_id=34)]):
                result = b.data(Args(refresh='iceberg.marts.dynamic_orders', sql=override, allow_shrink=True))
            self.assertEqual(result['wap_action'], 'refresh')
            self.assertTrue(result['valid'])
            self.assertEqual(result['generated_sql'], override or saved)
            self.assertEqual(result['sources'], ['raw.updated' if override else 'raw.orders'])
            self.assertEqual(result['previous_snapshot_id'], 12)
            self.assertEqual(result['published_snapshot_id'], 34)
            self.assertEqual(execute.call_args_list[0].args[1], 'CREATE SCHEMA IF NOT EXISTS iceberg.sandbox')
            self.assertEqual(execute.call_args_list[1].args[1],
                             'CREATE OR REPLACE TABLE "iceberg"."sandbox"."dynamic_orders" AS ' + (override or saved))
            self.assertEqual(execute.call_args_list[-1].args[1],
                             'CREATE OR REPLACE TABLE "iceberg"."marts"."dynamic_orders" AS SELECT * FROM "iceberg"."sandbox"."dynamic_orders" FOR VERSION AS OF 23')
            entry = b.load_wap_registry()['dynamic_orders']
            self.assertEqual(entry['intent'], 'count orders')
            self.assertEqual(entry['sql'], override or saved)
            self.assertEqual(entry['sources'], result['sources'])
            self.assertEqual(entry['staged_snapshot_id'], 23)
            self.assertEqual(entry['published_snapshot_id'], 34)
        with self.assertRaisesRegex(ValueError, 'no recorded WAP model SQL for unknown'):
            b.data(Args(refresh='unknown'))

    def test_refresh_checks_quality_regression_and_sql_policy(self):
        b.save_wap_entry('orders', sql='SELECT * FROM raw.orders')
        for count, nulls, minimum, allow_shrink, valid in (
                (0, 0, 1, False, False), (10, 1, 1, False, False),
                (10, 0, 20, False, False), (49, 0, 1, False, False),
                (49, 0, 1, True, True), (50, 0, 1, False, True)):
            def execute(_url, statement):
                return {'rows': [[100]] if statement.startswith('SELECT COUNT') else
                        [['id']] if statement.startswith('SHOW COLUMNS') else
                        [['orders']] if 'information_schema.tables' in statement else []}
            with self.subTest(count=count, nulls=nulls, minimum=minimum, allow_shrink=allow_shrink), \
                 patch.object(b, 'sql', side_effect=execute) as sql, \
                 patch.object(b, 'latest_snapshot', return_value=12), \
                 patch.object(b, 'audit_table', side_effect=[dict(row_count=count, null_counts={'id': nulls},
                     columns=[{'name': 'id'}]), dict(snapshot_id=34)]):
                before = b.load_wap_registry()
                result = b.data(Args(refresh='orders', min_rows=minimum, not_null='id', allow_shrink=allow_shrink))
                self.assertEqual(result['valid'], valid)
                writes = [c.args[1] for c in sql.call_args_list if c.args[1].startswith('CREATE OR REPLACE TABLE "iceberg"."marts"')]
                self.assertEqual(len(writes), int(valid))
                if not valid:
                    self.assertEqual(b.load_wap_registry(), before)
        for statement in ('DELETE FROM raw.orders', 'SELECT 1; DROP TABLE raw.orders'):
            with patch.object(b, 'sql') as execute, self.assertRaises(ValueError):
                b.data(Args(refresh='orders', sql=statement))
            execute.assert_not_called()

    def test_main_refresh_flag(self):
        with patch.object(sys, 'argv', ['bears', 'data', '--refresh', 'orders']), \
             patch.object(b, 'data', return_value={}) as data, \
             patch.object(b.wr, 'telemetry', return_value='recorded'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(), 0)
        self.assertEqual(data.call_args.args[0].refresh, 'orders')

    def test_audit_table(self):
        for columns in ([['a', 'bigint'], ['dotted.col', 'varchar']], []):
            responses = [{'rows': columns}, {'rows': [[3, 0, 2] if columns else [3]]}]
            responses.extend([{'rows': [[1, 'x']]}, {'rows': [[123]]}])
            with patch.object(b, 'sql', side_effect=responses) as execute:
                audit = b.audit_table('url', 'iceberg.sandbox.custom')
            self.assertEqual(audit['row_count'], 3)
            self.assertEqual(audit['column_count'], len(columns))
            self.assertEqual(audit['null_counts'], {'a': 0, 'dotted.col': 2} if columns else {})
            self.assertEqual(audit['snapshot_id'], 123)
            self.assertTrue(audit['quality_passed'])
            self.assertEqual(execute.call_count, 4)
            self.assertEqual(execute.call_args_list[0].args[1], 'SHOW COLUMNS FROM "iceberg"."sandbox"."custom"')
            self.assertEqual(execute.call_args_list[2].args[1], 'SELECT * FROM "iceberg"."sandbox"."custom" LIMIT 5')
            self.assertIn('"custom$history"', execute.call_args.args[1])
            if columns:
                self.assertEqual(execute.call_args_list[1].args[1],
                                 'SELECT COUNT(*), COUNT(*) - COUNT("a"), COUNT(*) - COUNT("dotted.col") FROM "iceberg"."sandbox"."custom"')

    def test_audit_empty_table_still_samples_and_escapes_columns(self):
        with patch.object(b, 'sql', side_effect=[{'rows': [['a"b', 'bigint']]},
                 {'rows': [[0, 0]]}, {'rows': []}, {'rows': [[11]]}]) as execute:
            audit = b.audit_table('url', 'iceberg.sandbox.empty')
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(audit['snapshot_id'], 11)
        self.assertIn('COUNT("a""b")', execute.call_args_list[1].args[1])
        self.assertEqual(audit['sample_rows'], [])
        self.assertFalse(audit['quality_passed'])

    def test_explicit_sql_stage_and_dry_run(self):
        for stage in (None, 'custom', 'iceberg.sandbox.custom', 'sandbox.custom'):
            with patch.object(b, 'sql', return_value={'rows': []}) as execute, \
                 patch.object(b, 'audit_table', return_value={'quality_passed': True}) as audit, \
                 patch.object(b.wr, 'http_json') as model:
                result = b.data(Args(request='', sql='SELECT 42; ', stage_table=stage))
            self.assertTrue(result['valid'])
            model.assert_not_called()
            if stage:
                self.assertEqual(result['staged_table'], 'iceberg.sandbox.custom')
                self.assertEqual(result['wap_action'], 'stage')
                self.assertEqual(execute.call_args_list[1].args[1], 'CREATE SCHEMA IF NOT EXISTS iceberg.sandbox')
                self.assertEqual(execute.call_args.args[1], 'CREATE OR REPLACE TABLE "iceberg"."sandbox"."custom" AS SELECT 42')
                audit.assert_called_once()
            else:
                self.assertNotIn('wap_action', result)
                execute.assert_called_once_with('http://trino', 'EXPLAIN SELECT 42')
                audit.assert_not_called()

    def test_stage_validation_and_quality_failures(self):
        for statement in ('SELECT 1; DELETE FROM x', 'WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x', 'DELETE FROM x'):
            with patch.object(b, 'sql') as execute:
                self.assertFalse(b.data(Args(request='', sql=statement, stage_table='t'))['valid'])
                execute.assert_not_called()
        with patch.object(b, 'sql', side_effect=RuntimeError('bad syntax')) as execute:
            self.assertFalse(b.data(Args(sql='SELECT bad', stage_table='t'))['valid'])
            self.assertEqual(execute.call_count, 1)
        with patch.object(b, 'sql', return_value={}), patch.object(b, 'audit_table', return_value={'quality_passed': False}):
            self.assertFalse(b.data(Args(sql='SELECT 1', stage_table='t'))['valid'])

    def test_publish_and_empty_source(self):
        for name in ('custom', 'sandbox.custom', 'marts.custom', 'iceberg.sandbox.custom', 'iceberg.marts.custom'):
            for previous in (123, None):
                with patch.object(b, 'audit_table', side_effect=[{'row_count': 3, 'null_counts': {}, 'columns': []}, {'snapshot_id': 456}]), \
                     patch.object(b, 'latest_snapshot', side_effect=[42, previous] if previous is not None else [42]), \
                     patch.object(b, 'sql', side_effect=[{'rows': [['custom']]}, {'rows': [[3]]}, {'rows': []}, {'rows': []}]
                                  if previous is not None else [{'rows': []}, {'rows': []}]) as execute:
                    result = b.data(Args(publish=name))
                self.assertEqual(result['previous_snapshot_id'], previous)
                self.assertEqual(result['published_snapshot_id'], 456)
                self.assertEqual(result['wap_action'], 'publish')
                self.assertTrue(result['valid'])
                self.assertEqual(execute.call_count, 4 if previous is not None else 2)
                self.assertEqual(execute.call_args.args, ('http://trino', 'CREATE OR REPLACE TABLE "iceberg"."marts"."custom" AS SELECT * FROM "iceberg"."sandbox"."custom" FOR VERSION AS OF 42'))
        with patch.object(b, 'audit_table', return_value={'row_count': 0, 'null_counts': {}}), \
             patch.object(b, 'latest_snapshot', return_value=42), patch.object(b, 'sql') as execute:
            result = b.data(Args(publish='custom'))
            self.assertFalse(result['valid'])
            self.assertIn('row_count must be > 0', result['error'])
            execute.assert_not_called()

    def test_publish_regression_guard(self):
        for count, target_cols, allow_shrink, valid, error in (
                (49, ['id'], False, False, '>50% shrink'),
                (49, ['id'], True, True, ''),
                (50, ['id'], False, True, ''),
                (100, ['id', 'region'], False, False, 'missing columns region'),
                (100, ['id', 'region'], True, True, '')):
            source = dict(row_count=count, columns=[{'name': 'id'}], null_counts={'id': 0})
            def execute(_url, statement):
                if statement.startswith('SELECT COUNT(*)'):
                    return {'rows': [[100]]}
                if statement.startswith('SHOW COLUMNS'):
                    return {'rows': [[c] for c in target_cols]}
                if 'information_schema.tables' in statement:
                    return {'rows': [['custom']]}
                return {'rows': []}
            with self.subTest(count=count, target_cols=target_cols, allow_shrink=allow_shrink), \
                 patch.object(b, 'audit_table', side_effect=[source, {'snapshot_id': 456}]), \
                 patch.object(b, 'latest_snapshot', return_value=123), \
                 patch.object(b, 'sql', side_effect=execute) as sql:
                result = b.data(Args(publish='custom', allow_shrink=allow_shrink))
            self.assertEqual(result['valid'], valid)
            self.assertEqual(result['previous_snapshot_id'], 123)
            writes = [c for c in sql.call_args_list if c.args[1].startswith('CREATE')]
            self.assertEqual(len(writes), int(valid))
            if error:
                self.assertIn(error, result['error'])

    def test_publish_blocks_baseline_query_failures(self):
        for responses in ([RuntimeError('count unavailable')],
                          [{'rows': [[100]]}, RuntimeError('columns unavailable')],
                          [{'error': 'count failed'}],
                          [{'rows': [[100]]}, {'error': 'columns failed'}]):
            source = dict(row_count=100, columns=[{'name': 'id'}], null_counts={}, snapshot_id=42)
            with self.subTest(responses=responses), \
                 patch.object(b, 'audit_table', return_value=source) as audit, \
                 patch.object(b, 'latest_snapshot', return_value=123), \
                 patch.object(b, 'sql', side_effect=[{'rows': [['custom']]}] + responses) as sql, \
                 patch.object(b, 'save_wap_entry') as save:
                result = b.data(Args(publish='custom'))
            self.assertFalse(result['valid'])
            self.assertEqual(result['previous_snapshot_id'], 123)
            self.assertEqual(result['audit'], source)
            self.assertIn('publish blocked: failed to query baseline table for regression check (', result['error'])
            self.assertFalse(any(c.args[1].startswith('CREATE') for c in sql.call_args_list))
            audit.assert_called_once_with('http://trino', 'iceberg.sandbox.custom', snapshot_id=123)
            save.assert_not_called()

    def test_publish_pins_audited_snapshot(self):
        for snapshot in (42, 9007199254740993):
            with self.subTest(snapshot=snapshot), \
                 patch.object(b, 'audit_table', side_effect=[
                     dict(row_count=3, null_counts={}, snapshot_id=snapshot), dict(snapshot_id=456)]), \
                 patch.object(b, 'latest_snapshot', return_value=snapshot), \
                 patch.object(b, 'sql', return_value={'rows': []}) as sql:
                self.assertTrue(b.data(Args(publish='custom'))['valid'])
            statement = ('CREATE OR REPLACE TABLE "iceberg"."marts"."custom" AS SELECT * FROM '
                         '"iceberg"."sandbox"."custom"')
            if snapshot is not None:
                statement += f' FOR VERSION AS OF {snapshot}'
            self.assertEqual(sql.call_args.args, ('http://trino', statement))

    def test_publish_quality_assertions(self):
        for rows, minimum, not_null, nulls, valid, error in (
                (2, 3, '', {}, False, '>= 3'),
                (3, 3, ' region,segment ', {'region': 0, 'segment': 0}, True, ''),
                (3, 1, 'region', {'region': 1}, False, 'not-null assertion failed: region'),
                (3, 1, 'missing', {}, False, 'not-null column missing: missing'),
                (0, -1, '', {}, False, 'row_count must be > 0'),
                (1, 0, '', {}, True, '')):
            source = dict(row_count=rows, null_counts=nulls)
            with self.subTest(rows=rows, minimum=minimum, not_null=not_null), \
                 patch.object(b, 'audit_table', side_effect=[source, {'snapshot_id': 456}]), \
                 patch.object(b, 'latest_snapshot', return_value=42) as snapshot, \
                 patch.object(b, 'sql', return_value={'rows': []}) as sql:
                result = b.data(Args(publish='t', min_rows=minimum, not_null=not_null, allow_shrink=True))
            self.assertEqual(result['valid'], valid)
            self.assertEqual(source['quality_passed'], valid)
            if error:
                self.assertIn(error, result['error'])
                sql.assert_not_called()
                snapshot.assert_called_once_with('http://trino', 'iceberg.sandbox.t')

    def test_rollback_and_invalid_arguments(self):
        with patch.object(b, 'sql') as execute, patch.object(b, 'audit_table', return_value={}):
            self.assertEqual(b.data(Args(rollback='t', snapshot_id=0))['restored_snapshot_id'], 0)
        self.assertIn('rollback_to_snapshot(0)', execute.call_args.args[1])
        for name in ('custom', 'marts.custom', 'iceberg.marts.custom', 'other.custom'):
            with patch.object(b, 'sql', return_value={}) as execute, patch.object(b, 'audit_table', return_value={'snapshot_id': 12}):
                result = b.data(Args(rollback=name, snapshot_id='0012'))
            self.assertEqual(result['restored_snapshot_id'], 12)
            self.assertEqual(result['wap_action'], 'rollback')
            self.assertTrue(result['valid'])
            self.assertIn('EXECUTE rollback_to_snapshot(12)', execute.call_args.args[1])
        for args in (Args(), Args(rollback='t'), Args(rollback='t', snapshot_id='-1'),
                     Args(rollback='t', snapshot_id='1); DROP TABLE t'),
                     Args(sql='SELECT 1', stage_table='other.t'), Args(publish='t; DELETE'),
                     Args(rollback='other.catalog.schema.t', snapshot_id=12)):
            with self.subTest(args=args), patch.object(b, 'sql') as execute, self.assertRaises(ValueError):
                b.data(args)
            execute.assert_not_called()

    def test_data_parser_optional_request_and_wap_flags(self):
        with patch.object(sys, 'argv', ['bears', 'data', '--sql', 'SELECT 1', '--stage-table', 't', '--publish', 't', '--rollback', 't', '--snapshot-id', '12', '--allow-shrink', '--min-rows', '10', '--not-null', 'region,segment']), \
             patch.object(b, 'run', return_value={}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(), 0)
        args = run.call_args.args[0]
        self.assertEqual((args.request, args.sql, args.stage_table, args.publish, args.rollback, args.snapshot_id), ('', 'SELECT 1', 't', 't', 't', '12'))
        self.assertEqual((args.allow_shrink, args.min_rows, args.not_null), (True, 10, 'region,segment'))

    def test_generated_sql_self_correction(self):
        sources = [['marts', 'orders', 'amount', 'double']]
        for stage in (None, 'summary'):
            completions = [{'choices': [{'message': {'content': json.dumps({'generated_sql': statement})}}]}
                           for statement in ('SELECT missing FROM iceberg.marts.orders',
                                             'SELECT amount FROM iceberg.marts.orders')]
            responses = [{'rows': sources}, RuntimeError('Column missing cannot be resolved'), {'rows': []}]
            if stage:
                responses.extend([{'rows': []}, {'rows': []}])
            with patch.object(b.wr, 'http_json', side_effect=completions) as model, \
                 patch.object(b, 'sql', side_effect=responses) as execute, \
                 patch.object(b, 'audit_table', return_value={'quality_passed': True}):
                result = b.data(Args(request='show amounts', stage_table=stage))
            self.assertTrue(result['valid'])
            self.assertTrue(result['self_corrected'])
            self.assertEqual(result['generated_sql'], 'SELECT amount FROM iceberg.marts.orders')
            self.assertEqual(execute.call_args_list[2].args[1], 'EXPLAIN SELECT amount FROM iceberg.marts.orders')
            self.assertEqual(model.call_count, 2)
            retry = json.loads(model.call_args.args[1])
            context = json.loads(retry['messages'][1]['content'])
            self.assertEqual(context['sources'], sources)
            self.assertEqual(context['failed_sql'], 'SELECT missing FROM iceberg.marts.orders')
            self.assertIn('cannot be resolved', context['error_message'])
            self.assertIn('no QUALIFY', retry['messages'][0]['content'])

    def test_retry_is_bounded_and_does_not_retry_explicit_sql(self):
        for retry in ('SELECT missing', 'SELECT still_missing', RuntimeError('model unavailable'), '', 'DELETE FROM t'):
            with self.subTest(retry=retry), \
                 patch.object(b, 'generate_data_sql', return_value='SELECT missing'), \
                 patch.object(b, 'generate_data_sql_retry', side_effect=retry if isinstance(retry, Exception) else None,
                              return_value=retry) as model, \
                 patch.object(b, 'sql', side_effect=RuntimeError('invalid column')) as execute:
                result = b.data(Args(request='show amounts'))
            self.assertFalse(result['valid'])
            self.assertNotIn('self_corrected', result)
            model.assert_called_once()
            self.assertEqual(execute.call_count, 2 if retry == 'SELECT still_missing' else 1)
        with patch.object(b, 'generate_data_sql_retry') as model, \
             patch.object(b, 'sql', side_effect=RuntimeError('invalid column')):
            self.assertFalse(b.data(Args(sql='SELECT missing'))['valid'])
        model.assert_not_called()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ai_config_path = Path(temporary.name) / 'ai_config.json'
        for target, name, value in ((b, 'AI_CONFIG_PATH', self.ai_config_path),
                                    (b, '_ai_config', {})):
            config_patch = patch.object(target, name, value)
            config_patch.start()
            self.addCleanup(config_patch.stop)
        environment = patch.dict(b.os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        registry = patch.object(b, 'WAP_REGISTRY', Path(temporary.name) / 'wap_models.json')
        registry.start()
        self.addCleanup(registry.stop)
        self.connection = patch.object(b.wr, "trino", return_value=contextlib.nullcontext("http://trino"))
        self.connection.start()
        self.addCleanup(self.connection.stop)

    def test_ai_config_defaults_environment_and_persistence(self):
        config = b.load_ai_config()
        self.assertEqual(config, dict(provider='local-openai', model='qwen3.8-flash-next',
            api_url='http://127.0.0.1:8001/v1', api_key_env='OPENAI_API_KEY', api_key='',
            timeout=30, max_tokens=1024, temperature=0.0))
        with patch.dict(b.os.environ, {'BEAR_MODEL': 'env-model', 'BEAR_MODEL_URL': 'http://env/v1',
                                       'BEAR_API_KEY': 'env-secret'}):
            active = b.load_ai_config()
            self.assertEqual((active['model'], active['api_url'], active['api_key_env']),
                             ('env-model', 'http://env/v1', 'BEAR_API_KEY'))
        settings = dict(provider='custom', model='saved-model', api_url='https://example.test/v1',
                        api_key_env='CUSTOM_KEY', api_key='direct-secret', timeout=12,
                        max_tokens=77, temperature=0.5)
        self.assertEqual(b.save_ai_config(settings), settings)
        self.assertEqual(json.loads(self.ai_config_path.read_text()), settings)
        self.assertEqual(self.ai_config_path.stat().st_mode & 0o777, 0o600)
        with patch.object(b, '_ai_config', {}):
            self.assertEqual(b.load_ai_config(), settings)

    def test_ai_config_missing_invalid_and_unwritable_storage(self):
        for content in ('{bad json', '[]', 'null'):
            self.ai_config_path.write_text(content)
            self.assertEqual(b.load_ai_config()['timeout'], 30)
        self.ai_config_path.unlink()
        with patch.object(b, 'AI_CONFIG_PATH', self.ai_config_path.parent / 'missing' / 'config.json'):
            b.save_ai_config({'model': 'memory-model'})
            self.assertEqual(b.load_ai_config()['model'], 'memory-model')
            self.assertTrue(b.AI_CONFIG_PATH.exists())
        with patch.object(Path, 'write_text', side_effect=OSError('read-only')):
            with self.assertRaisesRegex(RuntimeError, 'failed to persist AI config: read-only'):
                b.save_ai_config({'model': 'still-active'})
        self.assertEqual(b.load_ai_config()['model'], 'memory-model')

    def test_failed_config_save_preserves_disk_and_memory(self):
        b.save_ai_config({'model': 'original'})
        original = self.ai_config_path.read_text()
        for operation in ('mkdir', 'write_text', 'replace'):
            with self.subTest(operation=operation), patch.object(Path, operation, side_effect=OSError('denied')):
                with self.assertRaisesRegex(RuntimeError, 'failed to persist AI config: denied'):
                    b.save_ai_config({'model': 'lost'})
            self.assertEqual(self.ai_config_path.read_text(), original)
            self.assertEqual(b._ai_config['model'], 'original')
            self.assertEqual(b.load_ai_config()['model'], 'original')

    def test_wap_save_fail_closed_and_restart(self):
        b.save_wap_entry('model_a', sql='SELECT 1')
        b.save_wap_entry('model_b', sql='SELECT 2')
        before = b.WAP_REGISTRY.read_text()
        for operation in ('mkdir', 'write_text', 'replace'):
            with self.subTest(operation=operation), patch.object(Path, operation, side_effect=OSError('denied')):
                with self.assertRaisesRegex(RuntimeError, 'failed to persist WAP registry: denied'):
                    b.save_wap_entry('model_a', sql='SELECT 3')
            self.assertEqual(b.WAP_REGISTRY.read_text(), before)
        self.assertEqual(b.load_wap_registry()['model_b']['sql'], 'SELECT 2')

    def test_state_path_durable_override_and_fail_closed(self):
        root = TOOLS.parents[1]
        with patch.object(Path, 'mkdir'), patch.object(b.wap.os, 'access', return_value=True):
            for name in ('ai_config.json', 'wap_models.json'):
                self.assertEqual(b.wap.state_path(name), root / 'var/shirokuma' / name)
        with patch.object(Path, 'mkdir', side_effect=OSError('read-only')):
            with self.assertRaisesRegex(RuntimeError, 'State directory is not writable:'):
                b.wap.state_path('wap_models.json')
        with patch.object(Path, 'mkdir'), patch.object(b.wap.os, 'access', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'State directory is not writable:'):
                b.wap.state_path('ai_config.json')
        with patch.dict(b.os.environ, {'SHIROKUMA_STATE_DIR': str(self.ai_config_path.parent / 'state')}):
            self.assertEqual(b.wap.state_path('ai_config.json'), self.ai_config_path.parent / 'state/ai_config.json')

    def test_resolve_api_key_precedence(self):
        with patch.dict(b.os.environ, {'CUSTOM_KEY': 'env-secret', 'OPENAI_API_KEY': 'openai-secret'}):
            self.assertEqual(b.resolve_api_key(dict(api_key='direct', api_key_env='CUSTOM_KEY')), 'direct')
            self.assertEqual(b.resolve_api_key(dict(api_key='', api_key_env='CUSTOM_KEY')), 'env-secret')
            self.assertIsNone(b.resolve_api_key(b.load_ai_config()))
            self.assertEqual(b.resolve_api_key(dict(provider='openai', api_key_env='OPENAI_API_KEY')),
                             'openai-secret')
            self.assertIsNone(b.resolve_api_key(dict(api_key_env='MISSING')))

    def test_extract_sql_formats(self):
        for content in (' {"generated_sql": " SELECT 1; "} ',
                        '```json\n{"generated_sql":"SELECT 1;"}\n```',
                        '```sql\nSELECT 1;\n```', 'SELECT 1;',
                        'Here is the query:\n```SQL\nSELECT 1;\n```\nDone.'):
            with self.subTest(content=content):
                self.assertEqual(b._extract_sql(content), 'SELECT 1')
        self.assertEqual(b._extract_sql('WITH t AS (SELECT 1) SELECT * FROM t;'),
                         'WITH t AS (SELECT 1) SELECT * FROM t')
        for content in ('{}', '{"generated_sql": null}'):
            with self.assertRaises(ValueError):
                b._extract_sql(content)

    def test_ai_provider_config_and_auth_headers(self):
        completion = dict(model='served', usage=dict(prompt_tokens=3, completion_tokens=2),
                          choices=[dict(message=dict(content='SELECT 1;'))])
        for provider in ('local-openai', 'openai', 'bedrock', 'custom'):
            for key in (None, 'direct-secret', 'env-secret'):
                with self.subTest(provider=provider, key=key), \
                     patch.dict(b.os.environ, {'CUSTOM_KEY': 'env-secret'}), \
                     patch.object(b.wr, 'http_json', return_value=completion) as http:
                    config = dict(provider=provider, model='requested', api_url='https://ai.test/v1/',
                                  timeout=9, max_tokens=83, temperature=0.4,
                                  api_key='direct-secret' if key == 'direct-secret' else '',
                                  api_key_env='CUSTOM_KEY' if key == 'env-secret' else 'MISSING')
                    usage = {}
                    self.assertEqual(b._complete_data_sql({'request': 'one'}, usage, config), 'SELECT 1')
                    self.assertEqual(http.call_args.args[0], 'https://ai.test/v1/chat/completions')
                    self.assertEqual(http.call_args.kwargs['timeout'], 9)
                    headers = http.call_args.args[2]
                    self.assertEqual(headers.get('Authorization'), 'Bearer ' + key if key else None)
                    body = json.loads(http.call_args.args[1])
                    self.assertEqual((body['model'], body['max_tokens'], body['temperature']),
                                     ('requested', 83, 0.4))
                    self.assertEqual('enable_thinking' in body, provider == 'local-openai')
                    self.assertEqual(usage['model_provider'], provider)

    def test_connection_credentials_stay_with_endpoint(self):
        active = dict(provider='openai', model='ping', api_url='https://old.example/v1',
                      api_key='saved-secret', api_key_env='OPENAI_API_KEY',
                      timeout=5, temperature=0, max_tokens=10)
        completion = dict(choices=[dict(message=dict(content='2'))])
        for change in ({'api_url': 'https://new.example/v1', 'provider': 'custom'},
                       {'api_url': 'https://new.example/v1'}, {'provider': 'custom'}):
            for key in (None, '********', '', 'new-secret'):
                supplied = {**change, **({'api_key': key} if key is not None else {})}
                with self.subTest(change=change, key=key), \
                     patch.object(b, 'load_ai_config', return_value=dict(active)), \
                     patch.dict(b.os.environ, {}, clear=True), \
                     patch.object(b.wr, 'http_json', return_value=completion) as http:
                    self.assertEqual(b.test_ai_connection(supplied)['status'], 'connected')
                    self.assertEqual(http.call_args.args[2].get('Authorization'),
                                     'Bearer new-secret' if key == 'new-secret' else None)
        with patch.dict(b.os.environ, {'OPENAI_API_KEY': 'environment-secret'}):
            for provider in ('custom', 'local-openai'):
                config = dict(provider=provider, api_key_env='OPENAI_API_KEY')
                self.assertIsNone(b.resolve_api_key(config))
                self.assertEqual(b.resolve_api_key({**config, 'api_key': 'explicit'}), 'explicit')
        with patch.object(b, 'load_ai_config', return_value=dict(active)), \
             patch.object(b.wr, 'http_json', return_value=completion) as http:
            b.test_ai_connection({'model': 'other'})
            self.assertEqual(http.call_args.args[2]['Authorization'], 'Bearer saved-secret')

    def test_generation_override_credentials_stay_with_endpoint(self):
        requests = []

        class CompletionHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, self.headers.get('Authorization'), body))
                response = json.dumps(dict(choices=[dict(message=dict(content='SELECT 1'))])).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), CompletionHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        endpoint = 'http://127.0.0.1:' + str(server.server_port)
        base = dict(provider='openai', model='old-model', api_url=endpoint + '/old',
                    api_key='', api_key_env='', timeout=5, temperature=0, max_tokens=10)
        credentials = ({'api_key': 'saved-secret'}, {'api_key_env': 'OPENAI_API_KEY'},
                       {'api_key_env': 'BEAR_API_KEY'})
        functions = (lambda config: b.generate_data_sql('trino', 'one', config=config),
                     lambda config: b.generate_data_sql_retry('trino', 'one', [],
                         'SELECT broken', 'unknown column', config=config))
        environment = {'OPENAI_API_KEY': 'openai-secret', 'BEAR_API_KEY': 'bear-secret',
                       'NEW_KEY': 'new-env-secret'}
        with patch.dict(b.os.environ, environment, clear=True), \
             patch.object(b, 'sql', return_value={'rows': []}):
            for credential in credentials:
                active = {**base, **credential}
                old_key = credential.get('api_key') or environment[credential['api_key_env']]
                for generate in functions:
                    for change in ({'api_url': endpoint + '/new'}, {'provider': 'custom'},
                                   {'api_url': endpoint + '/new', 'provider': 'custom'}):
                        for supplied, expected in (({}, None), ({'api_key': '********'}, None),
                                ({'api_key': ''}, None), ({'api_key': 'new-secret'}, 'new-secret'),
                                ({'api_key_env': 'NEW_KEY'}, 'new-env-secret')):
                            with self.subTest(credential=credential, change=change, supplied=supplied), \
                                 patch.object(b, 'load_ai_config', return_value=dict(active)):
                                self.assertEqual(generate({**change, **supplied}), 'SELECT 1')
                                path, authorization, _ = requests[-1]
                                self.assertEqual(path, change.get('api_url', base['api_url'])
                                                 .removeprefix(endpoint) + '/chat/completions')
                                self.assertEqual(authorization, 'Bearer ' + expected if expected else None)
                    with patch.object(b, 'load_ai_config', return_value=dict(active)):
                        self.assertEqual(generate({'model': 'new-model'}), 'SELECT 1')
                        self.assertEqual(requests[-1][1], 'Bearer ' + old_key)
                        self.assertEqual(requests[-1][2]['model'], 'new-model')

    def test_ai_connection_success_and_failure(self):
        completion = dict(choices=[dict(message=dict(content='2'))], usage=dict(prompt_tokens=4))
        with patch.object(b.wr, 'http_json', return_value=completion) as http, \
             patch.object(b.time, 'monotonic', side_effect=[10, 10.125]):
            result = b.test_ai_connection(dict(provider='custom', model='ping-model', api_key='secret'))
        self.assertEqual(result, dict(status='connected', provider='custom', model='ping-model',
                                     latency_ms=125, usage={'prompt_tokens': 4}))
        self.assertEqual(http.call_args.args[2]['Authorization'], 'Bearer secret')
        for response in (RuntimeError('offline'), {}):
            with patch.object(b.wr, 'http_json', side_effect=response if isinstance(response, Exception) else None,
                              return_value=response), \
                 patch.object(b.time, 'monotonic', side_effect=[10, 10.25]):
                result = b.test_ai_connection()
            self.assertEqual((result['status'], result['latency_ms']), ('failed', 250))
            self.assertTrue(result['error'])

    def test_metadata_limit_and_business_semantics(self):
        rows = [['marts', 'orders', 'column_' + str(i), 'double'] for i in range(1500)]
        completion = dict(choices=[dict(message=dict(content='SELECT 1'))])
        sources = []
        with patch.object(b.wr, 'execute', return_value={'rows': rows}) as execute, \
             patch.object(b.wr, 'http_json', return_value=completion) as http:
            b.generate_data_sql('url', 'monthly revenue', sources_rows=sources, config={'model': 'custom'})
        self.assertEqual(execute.call_args.kwargs['max_rows'], 10000)
        self.assertIn('information_schema.columns', execute.call_args.args[1])
        self.assertEqual(sources, rows)
        body = json.loads(http.call_args.args[1])
        self.assertEqual(len(json.loads(body['messages'][1]['content'])['sources']), 1500)
        self.assertEqual(body['model'], 'custom')
        prompt = body['messages'][0]['content']
        for term in ("status = 'COMPLETED'", 'CANCELLED', 'REFUNDED', 'total_amount',
                     'quantity * extended_price * (1 - discount)', "date_trunc('month', order_date)",
                     'duplicate row multiplication', 'orders', 'order_items', 'customers'):
            self.assertIn(term, prompt)

    def test_retry_tokens_reach_pawprints(self):
        completions = [dict(model='served', usage=dict(prompt_tokens=p, completion_tokens=c),
                            choices=[dict(message=dict(content=statement))])
                       for p, c, statement in ((10, 3, 'SELECT missing'), (20, 5, 'SELECT 1'))]
        with patch.object(b.wr, 'http_json', side_effect=completions), \
             patch.object(b, 'sql', side_effect=[{'rows': []}, RuntimeError('missing column'), {'rows': []}]), \
             patch.object(b.wr, 'telemetry', return_value='recorded') as telemetry:
            result = b.run(Args(bear='data', request='one'))
        self.assertTrue(result['self_corrected'])
        self.assertEqual((result['llm_usage']['prompt_tokens'], result['llm_usage']['completion_tokens']), (30, 8))
        self.assertEqual((telemetry.call_args.kwargs['prompt_tokens'], telemetry.call_args.kwargs['completion_tokens']), (30, 8))
        usage = {}
        with patch.object(b.wr, 'http_json', side_effect=[completions[0], dict(completions[1], usage={})]):
            b._complete_data_sql({}, usage)
            b._complete_data_sql({}, usage)
        self.assertEqual((usage['prompt_tokens'], usage['completion_tokens']), (10, 3))

    def test_ai_cli_config_and_test(self):
        out = io.StringIO()
        with patch.object(sys, 'argv', ['bears', 'ai', 'config', '--provider', 'openai',
                                      '--model', 'remote', '--url', 'https://ai.test/v1', '--key-env', 'TOKEN']), \
             contextlib.redirect_stdout(out):
            self.assertEqual(b.main(), 0)
        config = json.loads(out.getvalue())
        self.assertEqual((config['provider'], config['model'], config['api_url'], config['api_key_env']),
                         ('openai', 'remote', 'https://ai.test/v1', 'TOKEN'))
        b.save_ai_config({'api_key': 'hidden-secret'})
        out = io.StringIO()
        with patch.object(sys, 'argv', ['bears', 'ai', 'config']), contextlib.redirect_stdout(out):
            self.assertEqual(b.main(), 0)
        self.assertNotIn('hidden-secret', out.getvalue())
        for status in ('connected', 'failed'):
            result = dict(status=status)
            if status == 'failed':
                result['error'] = 'offline'
            out = io.StringIO()
            with patch.object(sys, 'argv', ['bears', 'ai', 'test']), \
                 patch.object(b, 'test_ai_connection', return_value=result), contextlib.redirect_stdout(out):
                self.assertEqual(b.main(), int(status == 'failed'))
            self.assertEqual(json.loads(out.getvalue()), result)

    def test_general_sql_and_plan_analysis(self):
        plan = {"rows": [["Fragment 0 TableScan REPLICATED PARTITIONED"]], "query_id": "new"}
        with patch.object(b, "sql", return_value=plan) as execute:
            result = b.query(Args(target="explain", sql="SELECT x FROM iceberg.other.any_table"))
        self.assertEqual(execute.call_args.args[1], "EXPLAIN (TYPE DISTRIBUTED) SELECT x FROM iceberg.other.any_table")
        self.assertEqual(len(result["analysis"]["recommendations"]), 3)

    def test_query_id_lookup_escapes_input_and_retrieves_stats(self):
        stats = {"columns": [{"name": "query_id"}, {"name": "query"}], "rows": [["id", "SELECT 8"]]}
        with patch.object(b, "sql", side_effect=[stats, {"rows": [["Fragment 0"]]}]) as execute, patch.object(b.wr, "http_json", return_value={"queryStats": {}}):
            result = b.query(Args(target="id'other", sql=None))
        self.assertIn("'id''other'", execute.call_args_list[0].args[1])
        self.assertEqual(result["query_id"], "id")
        self.assertIn("execution", result["analysis"]["statistics"])

    def test_latest_missing_query(self):
        with patch.object(b, "sql", return_value={"rows": []}) as execute:
            with self.assertRaisesRegex(ValueError, "not found"):
                b.query(Args(target="latest", sql=None))
        self.assertIn("source <>", execute.call_args.args[1])

    def test_data_uses_live_sources_and_validates_generated_sql(self):
        for statement, valid in [("SELECT sum(amount) FROM iceberg.marts.custom", True), ("DELETE FROM x", False)]:
            completion = {"choices": [{"message": {"content": json.dumps({"generated_sql": statement})}}]}
            with patch.object(b.wr, "http_json", return_value=completion) as model, patch.object(b, "sql", return_value={"rows": [["marts", "custom", "amount", "double"]]}) as execute:
                result = b.data(Args(request="sum arbitrary amounts"))
            self.assertEqual(result["valid"], valid)
            self.assertIn("custom", model.call_args.args[1].decode())
            if valid:
                self.assertEqual(execute.call_args.args[1], "EXPLAIN " + statement)

    def test_invalid_sql_validation(self):
        completion = {"choices": [{"message": {"content": '{"generated_sql":"SELECT missing"}'}}]}
        with patch.object(b.wr, "http_json", return_value=completion), patch.object(b, "sql", side_effect=[{"rows": []}, RuntimeError("invalid column")]):
            self.assertFalse(b.data(Args(request="anything"))["valid"])

    def test_catalog_search_and_arbitrary_table(self):
        with patch.object(b, "sql", return_value={"rows": [["metadata"]]}) as execute, patch.object(b.wr, "kubectl", return_value="{}"):
            result = b.catalog(Args(action="search", table=None))
            self.assertIn("smoke", result["metadata"])
            result = b.catalog(Args(action="describe", table="custom.strange"))
        self.assertIn("snapshots", result["metadata"])
        self.assertIn('"iceberg"."custom"."strange$snapshots"', execute.call_args.args[1])

    def test_policy_manifest_and_baseline(self):
        container = {"name": "app", "securityContext": {"readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}, "resources": {"limits": {"cpu": "1", "memory": "1Gi"}}}
        spec = {"securityContext": {"runAsNonRoot": True, "runAsUser": 1000}, "containers": [container]}
        workload = {"metadata": {"name": "custom"}, "spec": {"template": {"spec": spec}}}
        with patch.object(b.wr, "kubectl", return_value=json.dumps(workload)) as kubectl:
            self.assertEqual(b.policy(Args(action="check", manifest="arbitrary.yaml"))["advisory"], "allow")
            self.assertIn("--dry-run=client", kubectl.call_args.args)
        spec["initContainers"] = [{"name": "unsafe"}]
        with patch.object(b.wr, "kubectl", return_value=json.dumps({"items": [workload]})):
            result = b.policy(Args(action="baseline"))
        self.assertEqual(result["advisory"], "deny")
        self.assertEqual(len(result["violations"]), 3)

    def test_telemetry_on_every_bear_and_failure(self):
        for name, identity in b.NAMES.items():
            with patch.object(b, name, return_value={}), patch.object(b.wr, "telemetry", return_value="recorded") as telemetry:
                self.assertEqual(b.run(Args(bear=name))["bear"], identity)
                self.assertEqual(telemetry.call_args.args[0], "bear." + identity)
                self.assertEqual(telemetry.call_args.kwargs, {"prefix": ""})
        with patch.object(b, "sre", side_effect=RuntimeError("offline")), patch.object(b.wr, "telemetry", return_value="skipped") as telemetry:
            result = b.run(Args(bear="sre"))
            self.assertEqual(result["telemetry"], "skipped")
            self.assertEqual(telemetry.call_args.args[2], "failed")

    def test_llm_usage_flows_into_generated_and_staged_results(self):
        for stage in (None, 'orders_summary'):
            for metadata in ({}, {'model': 'served-model'}, {'usage': {'prompt_tokens': 0, 'completion_tokens': 12}},
                             {'model': 'served-model', 'usage': {'prompt_tokens': 25, 'completion_tokens': 8}}):
                with self.subTest(stage=stage, metadata=metadata):
                    completion = dict(metadata, choices=[{'message': {'content': '{"generated_sql":"SELECT 42"}'}}])
                    with patch.object(b.wr, 'http_json', return_value=completion), \
                         patch.object(b, 'sql', return_value={'rows': []}), \
                         patch.object(b, 'audit_table', return_value={'quality_passed': True, 'snapshot_id': 9}):
                        result = b.data(Args(request='count orders', stage_table=stage))
                    if metadata:
                        usage = result['llm_usage']
                        self.assertEqual(usage['model_provider'], 'local-openai')
                        if metadata.get('model'):
                            self.assertEqual(usage['model_name'], metadata['model'])
                        else:
                            self.assertTrue(usage['model_name'])
                        self.assertEqual(usage['prompt_tokens'], metadata.get('usage', {}).get('prompt_tokens'))
                        self.assertEqual(usage['completion_tokens'], metadata.get('usage', {}).get('completion_tokens'))
                    else:
                        self.assertNotIn('llm_usage', result)

    def test_rich_wap_telemetry(self):
        cases = [({'staged_table': 'iceberg.sandbox.t', 'audit': {'snapshot_id': 0}, 'wap_action': 'stage'},
                  {'target': 'iceberg.sandbox.t@0'}),
                 ({'table': 'iceberg.marts.t', 'published_snapshot_id': 42, 'audit': {'snapshot_id': 1}, 'wap_action': 'publish'},
                  {'target': 'iceberg.marts.t@42', 'risk_level': 'normal'}),
                 ({'table': 'iceberg.marts.t', 'restored_snapshot_id': 7, 'wap_action': 'rollback'},
                  {'target': 'iceberg.marts.t@7', 'risk_level': 'normal'}),
                 ({'table': 'x' * 300, 'audit': None}, {'target': 'x' * 256}),
                 ({'llm_usage': {'model_provider': 'local-openai', 'model_name': 'test', 'prompt_tokens': 0, 'completion_tokens': 8}},
                  {'model_provider': 'local-openai', 'model_name': 'test', 'prompt_tokens': 0, 'completion_tokens': 8}),
                 ({'llm_usage': {'model_name': '', 'prompt_tokens': True, 'completion_tokens': -1}}, {}),
                 ({'llm_usage': {'prompt_tokens': 1.5, 'completion_tokens': None}}, {})]
        for report, expected in cases:
            with self.subTest(report=report), patch.object(b, 'data', return_value=dict(report)), \
                 patch.object(b.wr, 'telemetry', return_value='recorded') as telemetry:
                b.run(Args(bear='data'))
                self.assertEqual(telemetry.call_args.kwargs, dict(prefix='', **expected))

    def test_main_clean_json(self):
        out = io.StringIO()
        with patch.object(sys, "argv", ["bears", "query", "--sql", "SELECT 42"]), patch.object(b, "query", return_value={}), patch.object(b.wr, "telemetry", return_value="recorded"), contextlib.redirect_stdout(out):
            self.assertEqual(b.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["bear"], "query_doctor")

    def test_sre_readiness_and_observability_failure(self):
        obj = {"kind": "Deployment", "metadata": {"name": "agenthouse", "namespace": "telemetry"}, "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}
        loki = dict(obj, metadata={"name": "loki", "namespace": "telemetry"})
        def kubectl(*args):
            if args[:2] == ("get", "deploy,sts"):
                return json.dumps({"items": [obj, loki]})
            if args[0] == "get" and args[1] != "--raw":
                return json.dumps(obj)
            if "--raw" in args:
                return "{}"
            return "metrics or logs"
        with patch.object(b.wr, "kubectl", side_effect=kubectl), patch("agenthouse.manage.Client") as client:
            client.return_value.query.return_value = "[]"
            self.assertEqual(b.sre(Args())["status"], "healthy")
            client.return_value.query.side_effect = RuntimeError("ClickHouse unavailable")
            self.assertEqual(b.sre(Args())["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
