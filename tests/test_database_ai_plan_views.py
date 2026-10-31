"""VIEW fences with genuine isolated SQLite authority and synthetic REST/HTTP."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import io
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai_plan as a2
        import db_ai as ai
        import db_ai_plan as plan
        import db_ai_plan_views as views
        import control_store as store
        import logical_registry as lr
        import trino_auth
        from query_context import QueryError

_prepare = trino_auth.prepare_backend_v2
_complete = ai._complete


class ViewPlanTests(unittest.TestCase):
    patch = a2.PlanTests.patch
    cleanup = a2.PlanTests.cleanup
    backend = a2.PlanTests.backend
    send = a2.PlanTests.send
    accept = a2.PlanTests.accept
    run_parent = a2.PlanTests.run_parent
    record = a2.PlanTests.record
    error = a2.PlanTests.error

    def prepare(self, *, principal, database_id, selected_role):
        return _prepare(principal=principal, database_id=database_id, selected_role=selected_role)

    def setUp(self):
        a2.PlanTests.setUp(self)
        # Same durable rows/invariants as LogicalRegistryTests/LogicalAccessTests;
        # retain the existing A1 catalog and query test transports.
        with store.connect() as db:
            for database in self.f.databases:
                env = 'env-' + database.sql_catalog
                generation = 'g-' + database.sql_catalog
                db.execute("UPDATE warehouse_config_generations SET state='APPLIED' WHERE id=?", (generation,))
                db.execute("INSERT INTO operations VALUES (?,'alice','create','[]',1,'APPLIED','[]',NULL,'{}','{}')",
                    ('op-' + database.id,))
                db.execute('''INSERT INTO db_creations
                    (id,environment_id,warehouse_id,actor,enrollment_id,enrollment_revision,database_id,
                     create_operation_id,intent_json,child_ids_json,revision,reservation_active,
                     created_at,updated_at,creation_mode,logical_state)
                    VALUES (?,?,'LOCAL','alice','enrollment',1,?,?,'{}','{}',1,1,'now','now','logical_v1','READY')''',
                    ('root-' + database.id, env, database.id, 'op-' + database.id))
                lr.put_foundation(db, dict(environment_id=env, warehouse_id='LOCAL', generation_id=generation,
                    revision=1, status='READY', config_ref='config', verification_ref='verified'))
                lr.put_binding(db, dict(database_id=database.id, environment_id=env, warehouse_id='LOCAL',
                    foundation_revision=1, desired_policy_revision=1, applied_policy_revision=1,
                    catalog_credential_ref='catalog', verification_ref='verified', revision=1))
                db.execute("INSERT INTO db_creation_principal_gates VALUES (?, 'owner','ACTIVE',1,NULL,NULL,'now')", (env,))
                for actor in ('alice', 'bob', 'owner'):
                    for role in ('data_engineer', 'bi_viewer', 'admin'):
                        if actor == 'owner' and role != 'admin':
                            continue
                        ident = '-'.join((database.sql_catalog, actor, role))
                        candidate = lr.put_identity(db, dict(id=ident, principal_id=actor, database_id=database.id,
                            selected_role=role, grant_epoch=None if actor == 'owner' else 1, policy_revision=1,
                            backend_username=ident, credential_ref=ident, state='CANDIDATE',
                            verification_ref=None, revision=1, authority_source='platform_admin' if actor == 'owner'
                            else 'grant', account_gate_revision=1 if actor == 'owner' else None))
                        lr.put_identity(db, dict(candidate, state='ACTIVE', verification_ref='verified', revision=2))
        self.patch('trino_auth.password_mode', return_value=True)
        self.patch('logical_registry.identity_credentials', side_effect=lambda i:
            dict(username=i['backend_username'], password='PRIVATE'))
        self.patch('trino_auth.load_database_credentials', side_effect=AssertionError('legacy credentials'))
        self.patch('logical_registry.platform_admin_credentials', side_effect=AssertionError('internal observer'))
        self.patch('database_inventory.catalog_api', self.catalog)
        # REST TABLE identities are UUIDs, shared by discovery and snapshot reads.
        for key, metadata in self.f.objects.items():
            if key[3] == 'TABLE':
                metadata['table-uuid'] = str(uuid.uuid4())
        self.pointer = 's3://PRIVATE/metadata.json'
        self.view('order_view', 'SELECT * FROM sales.raw.orders')

    @contextmanager
    def catalog(self, database, binding):
        with self.f.catalog(database, binding) as api:
            def wrapped(path):
                response = api(path)
                if 'metadata' in response:
                    response['metadata-location'] = self.pointer
                return response
            yield wrapped

    def view(self, name, sql, schema='raw', catalog='sales', defaults=('sales', ['raw'])):
        value = self.f.metadata(name, 'VIEW')
        value['view-uuid'] = str(uuid.uuid4())
        value['properties'] = {'comment': 'PRIVATE OWNER NOTE'}
        current = value['versions'][0]
        current['representations'] = [dict(type='sql', dialect='trino', sql=sql)]
        if defaults is not None:
            current.update({'default-catalog': defaults[0], 'default-namespace': defaults[1]})
        self.f.objects[(catalog, schema, name, 'VIEW')] = value
        return value

    def capture(self, sql='SELECT * FROM order_view', context=None, principal=None):
        return views.capture(principal or self.principal, context or self.context, sql)

    def frozen_view(self, ids=(731234567891,), schema='raw'):
        table = self.f.objects[('sales', schema, 'orders', 'TABLE')]
        table['snapshots'] = [dict(**{'snapshot-id': ident, 'schema-id': 1}) for ident in ids]
        self.view('order_view', ' UNION ALL '.join(
            f'SELECT * FROM sales.{schema}.orders FOR VERSION AS OF {ident}' for ident in ids))
        return table

    def test_native_numeric_snapshot_view_plan_and_private_evidence(self):
        self.frozen_view()
        self.f.objects[('sales', 'raw', 'order_view', 'VIEW')].pop('properties')
        pins, _, _, dependencies = self.capture()
        table = next(p for p in pins if p['kind'] == 'TABLE')
        with store.connect() as db:
            obj = ai.registry.get_object(db, table['ref']['object_id'])
        self.assertEqual(obj.backend_identity, self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['table-uuid'])
        self.assertEqual([s['snapshot_id'] for s in table['snapshots']], [731234567891])
        self.assertEqual(set(table['snapshots'][0]), {'snapshot_id', 'schema_fingerprint'})
        self.assertEqual([d['table'] for d in dependencies], ['orders'])
        parent = self.accept('SELECT * FROM order_view')
        result = self.run_parent()
        self.assertEqual((result['state'], result['plan_status']), ('SUCCEEDED', 'COLLECTED'))
        self.assertEqual(self.calls[0][1], b'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) SELECT * FROM order_view')
        for secret in ('731234567891', 'FOR VERSION', 'PRIVATE', 'snapshot', 'properties', 's3://'):
            self.assertNotIn(secret, ai._json(result))
            self.assertNotIn(secret, str(self.f.complete.call_args))
        self.assertEqual(parent['metadata'], result['metadata'])

    def test_external_view_without_properties_is_invoker(self):
        metadata = self.view('external_report', 'SELECT id FROM sales.raw.orders')
        metadata.pop('properties')
        parent = self.accept('SELECT * FROM external_report')
        result = self.run_parent()
        self.assertEqual((result['state'], result['plan_status']), ('SUCCEEDED', 'COLLECTED'))
        self.assertEqual(result['metadata'], parent['metadata'])
        self.assertEqual([d['table'] for d in result['metadata']['view_dependencies']], ['orders'])
        self.assertEqual(self.calls[0][1],
            b'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) SELECT * FROM external_report')
        self.assertEqual(len(self.calls), 1)
        self.f.complete.assert_called_once()

    def test_null_malformed_properties_and_owner_presence_deny_before_admission(self):
        for properties in (None, [], 'PRIVATE', 1,
                {'trino.run-as-owner': False}, {'trino.owner': None}, {'owner': ''}):
            with self.subTest(properties=properties):
                metadata = self.view('order_view', 'SELECT * FROM sales.raw.orders')
                metadata['properties'] = properties
                self.rejected_before_admission()

    def test_snapshot_rest_table_replacement_with_identical_schema_and_snapshot_denied(self):
        table = self.frozen_view()
        original = views._snapshots
        def replaced(principal, context, pin, requested):
            # Discovery/detail captured the old UUID; only the REST identity changes.
            table['table-uuid'] = str(uuid.uuid4())
            return original(principal, context, pin, requested)
        with patch.object(views, '_snapshots', side_effect=replaced):
            self.rejected_before_admission('AI_METADATA_CHANGED', 409)

    def test_snapshot_registry_ref_incarnation_schema_and_kind_changes_denied(self):
        self.frozen_view()
        original = views._snapshots
        for field, value in (('incarnation', 99), ('id', str(uuid.uuid4())),
                ('schema_id', self.context.schema_id), ('kind', 'VIEW')):
            with self.subTest(field=field):
                def changed(principal, context, pin, requested):
                    # A same-schema mutation would be a no-op: use another real schema.
                    replacement = self.f.schemas[self.context.database_id]['marts'].id if field == 'schema_id' else value
                    with store.connect() as db:
                        db.execute('UPDATE object_bindings SET ' + field + '=? WHERE id=?',
                            (replacement, pin['ref']['object_id']))
                    try:
                        return original(principal, context, pin, requested)
                    finally:
                        with store.connect() as db:
                            db.execute('UPDATE object_bindings SET ' + field + '=? WHERE id=?',
                                (pin['ref']['object_id'] if field == 'id' else
                                 pin['ref']['incarnation'] if field == 'incarnation' else
                                 pin['schema_id'] if field == 'schema_id' else 'TABLE',
                                 replacement if field == 'id' else pin['ref']['object_id']))
                with patch.object(views, '_snapshots', side_effect=changed):
                    self.rejected_before_admission('AI_METADATA_CHANGED', 409)

    def test_snapshot_malformed_rest_uuid_denied_before_admission(self):
        table = self.frozen_view()
        original = views._snapshots
        for identity in (None, 'invalid', 42, []):
            with self.subTest(identity=identity):
                def malformed(principal, context, pin, requested):
                    old = table['table-uuid']
                    table['table-uuid'] = identity
                    try:
                        return original(principal, context, pin, requested)
                    finally:
                        table['table-uuid'] = old
                with patch.object(views, '_snapshots', side_effect=malformed):
                    self.rejected_before_admission()

    def test_recursive_shared_table_captures_every_distinct_snapshot_once(self):
        self.frozen_view((731234567891, 731234567892))
        self.view('left_view', 'SELECT * FROM sales.raw.orders FOR VERSION AS OF 731234567892')
        self.view('right_view', 'SELECT * FROM sales.raw.orders FOR VERSION AS OF 731234567891')
        self.view('nested_view', 'SELECT * FROM left_view UNION ALL SELECT * FROM right_view')
        self.view('order_view', 'SELECT * FROM nested_view UNION ALL SELECT * FROM left_view')
        pins, _, _, dependencies = self.capture()
        table = next(p for p in pins if p['table'] == 'orders')
        self.assertEqual([s['snapshot_id'] for s in table['snapshots']], [731234567891, 731234567892])
        self.assertEqual(sum(d['table'] == 'orders' for d in dependencies), 1)
        self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['snapshots'].pop()
        self.rejected_before_admission()

    def test_snapshot_missing_ambiguous_malformed_or_different_schema_before_admission(self):
        def historical(table):
            schema = deepcopy(table['schemas'][0])
            schema['schema-id'] = 2
            table['schemas'].append(schema)
            table['snapshots'][0]['schema-id'] = 2
            return schema
        def mismatched(table):
            historical(table)['fields'].reverse()
        def changed_type(table):
            historical(table)['fields'][0]['type'] = 'string'
        mutations = [lambda t: t.pop('snapshots'), lambda t: t.update(snapshots=[]),
            lambda t: t['snapshots'].append(deepcopy(t['snapshots'][0])),
            lambda t: t['snapshots'][0].update({'snapshot-id': '731234567891'}),
            lambda t: t['snapshots'][0].update({'schema-id': True}),
            lambda t: t['snapshots'][0].update({'schema-id': -1}),
            lambda t: t['snapshots'][0].update({'schema-id': 99}),
            lambda t: t['schemas'].append(deepcopy(t['schemas'][0])),
            lambda t: t.update(snapshots={}), mismatched, changed_type]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.f.objects[('sales', 'raw', 'orders', 'TABLE')] = self.f.metadata('orders', 'TABLE')
                self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['table-uuid'] = str(uuid.uuid4())
                mutation(self.frozen_view())
                self.rejected_before_admission()

    def test_snapshot_numeric_bounds_and_unsupported_version_forms_are_private(self):
        self.frozen_view((1, 2**63 - 1))
        self.assertEqual(len(next(p for p in self.capture()[0] if p['kind'] == 'TABLE')['snapshots']), 2)
        for value in ('0', '-1', '+1', str(2**63), '1.0', '1e2', "'731234567891'", 'TRUE',
                'FALSE', 'NULL', '?', ':snapshot', '(731234567891)', '731234567891 + 1',
                "TIMESTAMP '2026-10-31 00:00:00'", '１２３', "'private_tag'"):
            with self.subTest(value=value):
                self.view('order_view', 'SELECT * FROM sales.raw.orders FOR VERSION AS OF ' + value)
                output = io.StringIO()
                handler = logging.StreamHandler(output)
                logger = logging.getLogger('sqlglot')
                logger.addHandler(handler)
                try:
                    error = self.rejected_before_admission()
                finally:
                    logger.removeHandler(handler)
                self.assertNotIn('731234567891', output.getvalue() + error)
                self.assertNotIn('private_tag', output.getvalue() + error)
        for clause in ("FOR TIMESTAMP AS OF TIMESTAMP '2026-10-31 00:00:00'", 'FOR VERSION BETWEEN 1 AND 2'):
            self.view('order_view', 'SELECT * FROM sales.raw.orders ' + clause)
            self.rejected_before_admission()

    def test_equal_historical_columns_allowed_and_schema_replacement_changes_pin(self):
        table = self.frozen_view()
        before = self.capture()[0]
        historical = deepcopy(table['schemas'][0])
        historical['schema-id'] = 2
        table['schemas'].append(historical)
        table['snapshots'][0]['schema-id'] = 2
        after = self.capture()[0]
        self.assertEqual(before[1]['columns'], after[1]['columns'])
        self.assertNotEqual(before[1]['snapshots'], after[1]['snapshots'])
        self.accept('SELECT * FROM order_view')
        self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')

    def test_versioned_view_and_cte_references_are_unsupported(self):
        self.view('child_view', 'SELECT 1')
        for body in ('SELECT * FROM child_view FOR VERSION AS OF 123',
                'WITH orders AS (SELECT 1) SELECT * FROM orders FOR VERSION AS OF 123'):
            self.view('order_view', body)
            self.rejected_before_admission()

    def test_snapshot_cte_shadowing_and_qualified_physical_reference(self):
        self.frozen_view((123,))
        for body in (
                'WITH orders AS (SELECT * FROM sales.raw.orders FOR VERSION AS OF 123) SELECT * FROM orders',
                'WITH orders AS (SELECT 1) SELECT * FROM sales.raw.orders FOR VERSION AS OF 123',
                'WITH orders AS (SELECT 1) SELECT * FROM orders JOIN sales.raw.orders FOR VERSION AS OF 123 t ON TRUE'):
            self.view('order_view', body)
            pins = self.capture()[0]
            self.assertEqual([(p['schema'], p['table']) for p in pins], [('raw', 'order_view'), ('raw', 'orders')])
            self.assertEqual(pins[1]['snapshots'][0]['snapshot_id'], 123)
        self.view('order_view', 'WITH orders AS (SELECT 1) SELECT * FROM orders')
        self.assertEqual(len(self.capture()[0]), 1)

    def test_snapshot_schema_authority_precedes_any_child_table_rest(self):
        self.view('order_view', 'SELECT * FROM sales.raw.orders FOR VERSION AS OF 123 '
            'UNION ALL SELECT * FROM sales.hidden.customers FOR VERSION AS OF 456')
        self.f.catalog_calls.clear()
        self.rejected_before_admission('SQL_PERMISSION_DENIED', 403)
        self.assertFalse(any('/tables/' in path for _, path in self.f.catalog_calls))
        self.view('order_view', 'SELECT * FROM sales.raw.orders FOR VERSION AS OF 123', schema='marts')
        self.f.catalog_calls.clear()
        self.error('SQL_PERMISSION_DENIED', lambda: self.capture(context=self.f.ctx(role='bi_viewer', schema='marts')))
        self.assertFalse(any('/tables/' in path for _, path in self.f.catalog_calls))

    def test_append_current_head_and_pointer_changes_keep_snapshot_pins_equal(self):
        table = self.frozen_view()
        before = self.capture()[0]
        original = self.catalog
        @contextmanager
        def catalog(*args):
            with original(*args) as api:
                def wrapped(path):
                    response = api(path)
                    if '/tables/' in path:
                        response['metadata-location'] = 's3://PRIVATE/new-table-head.json'
                    return response
                yield wrapped
        table['snapshots'].append({'snapshot-id': 999999, 'schema-id': 1})
        table['current-snapshot-id'] = 999999
        with patch.object(ai.inventory, 'catalog_api', catalog):
            self.assertEqual(self.capture()[0], before)
            self.accept('SELECT * FROM order_view')
            self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')

    def test_snapshot_removal_inflight_changes_metadata_before_dispatch(self):
        table = self.frozen_view()
        parent = self.accept('SELECT * FROM order_view')
        table['snapshots'].clear()
        self.error('AI_METADATA_CHANGED', lambda: plan.check_metadata(self.principal, self.context,
            parent['id'], json.loads(ai._row(parent['id'])['pins'])['relations']))
        result = self.run_parent()
        self.assertEqual((result['error_code'], result['plan']['query_state']),
            ('AI_METADATA_CHANGED', 'NOT_SUBMITTED'))
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()

    def test_snapshot_removal_during_model_retains_valid_collection(self):
        table = self.frozen_view()
        self.accept('SELECT * FROM order_view')
        def complete(*args, **kwargs):
            table['snapshots'].clear()
            return 'PRIVATE stale explanation'
        self.f.complete.side_effect = complete
        result = self.run_parent()
        self.assertEqual((result['state'], result['plan_status'], result['error_code']),
            ('FAILED', 'COLLECTED', 'AI_METADATA_CHANGED'))
        self.assertEqual(result['plan']['query_state'], 'FINISHED')
        self.assertIsNotNone(result['plan']['operators'])
        self.assertIsNone(result['text'])

    def test_original_user_version_syntax_still_rejected_for_a1_and_a2(self):
        sql = 'SELECT * FROM sales.raw.orders FOR VERSION AS OF 123'
        self.error('AI_INPUT_INVALID', lambda: ai._parse(sql, self.context))
        self.error('AI_INPUT_INVALID', lambda: ai._capture(self.principal, self.context, sql))
        self.error('AI_INPUT_INVALID', lambda: self.accept(sql))
        self.error('AI_INPUT_INVALID', lambda: ai._parse(sql, self.context, _stored_versions={}))

    def rejected_before_admission(self, code='AI_PLAN_VIEW_UNSUPPORTED', status=400):
        with self.assertRaises(QueryError) as caught:
            self.accept('SELECT * FROM order_view')
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        with store.connect() as db:
            for table in ('ai_explanations', 'query_records', 'warehouse_admissions'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()
        return str(caught.exception)

    def test_private_unsupported_statements_never_log_definition(self):
        marker = 'PRIVATE_VIEW_STATEMENT_731'
        for body in (f'VACUUM {marker}', f'SELECT 1; VACUUM {marker}'):
            with self.subTest(body=body):
                self.view('order_view', body)
                output = io.StringIO()
                handler = logging.StreamHandler(output)
                logger = logging.getLogger('sqlglot')
                logger.addHandler(handler)
                try:
                    error = self.rejected_before_admission()
                finally:
                    logger.removeHandler(handler)
                self.assertNotIn(marker, output.getvalue() + error)
                self.assertIn('unsupported syntax', output.getvalue())

    def test_private_generation_failures_never_log_definition(self):
        from sqlglot.dialects.trino import Trino
        original = Trino.Generator.select_sql
        marker = 'PRIVATE_VIEW_GENERATION_827'
        for failing_call in (1, 2):  # private prompt, then canonical definition
            with self.subTest(failing_call=failing_call):
                self.view('order_view', f'SELECT 1 AS {marker}')
                calls = []
                def unsupported(generator, expression):
                    calls.append(expression)
                    if len(calls) == failing_call:
                        generator.unsupported(marker)
                    return original(generator, expression)
                output = io.StringIO()
                handler = logging.StreamHandler(output)
                logger = logging.getLogger('sqlglot')
                logger.addHandler(handler)
                try:
                    # Outer user SQL has no constant projection; count only the VIEW.
                    def generate(generator, expression):
                        if expression.find(views.sqlglot.exp.Literal) or expression.find(views.sqlglot.exp.Placeholder):
                            return unsupported(generator, expression)
                        return original(generator, expression)
                    with patch.object(Trino.Generator, 'select_sql', generate):
                        error = self.rejected_before_admission()
                finally:
                    logger.removeHandler(handler)
                self.assertEqual(len(calls), failing_call)
                self.assertNotIn(marker, output.getvalue() + error)

    def test_canonical_parse_is_private_and_sqlglot_errors_are_fixed(self):
        marker = 'PRIVATE_CANONICAL_998'
        self.view('order_view', 'SELECT 1')
        with patch.object(views.sqlglot, 'parse_one',
                side_effect=views.sqlglot.errors.ParseError(marker)) as parse:
            error = self.rejected_before_admission()
        self.assertEqual(parse.call_args.kwargs['error_message_context'], 0)
        self.assertNotIn(marker, error)

    def test_a1_parser_and_generation_defaults_remain_unchanged(self):
        from sqlglot.dialects.trino import Trino
        original = Trino.Generator.select_sql
        marker = 'A1_UNSUPPORTED_GENERATION'
        def unsupported(generator, expression):
            generator.unsupported(marker)
            return original(generator, expression)
        with patch.object(views.sqlglot,
                'parse', wraps=views.sqlglot.parse) as parse:
            with patch.object(Trino.Generator, 'select_sql', unsupported), self.assertLogs('sqlglot') as logs:
                result = ai._parse('SELECT 731 AS x /* comment */', self.context)
        self.assertEqual(result, ([], 'SELECT ? AS x'))
        self.assertNotIn('error_message_context', parse.call_args.kwargs)
        self.assertIn(marker, '\n'.join(logs.output))

    def test_decimal_bounds_and_nested_types_before_admission(self):
        for decimal in ('decimal(0,99)', 'decimal(39,0)', 'decimal(2,3)',
                'decimal(0,0)', 'decimal(38,39)', 'decimal(-1,0)', 'decimal(1,-1)'):
            for value in (decimal, {'type': 'list', 'element': decimal}):
                with self.subTest(value=value):
                    metadata = self.view('order_view', 'SELECT 1')
                    metadata['schemas'][0]['fields'][0]['type'] = value
                    self.rejected_before_admission()
        for decimal in ('decimal(1,0)', 'decimal(1,1)', 'decimal(38,0)', 'decimal(38,38)', 'decimal(2, 2)'):
            for value in (decimal, {'type': 'struct', 'fields': [dict(name='nested', type={
                    'type': 'map', 'key': 'string', 'value': {'type': 'list', 'element': decimal}})]}):
                with self.subTest(value=value):
                    metadata = self.view('order_view', 'SELECT 1')
                    metadata['schemas'][0]['fields'][0]['type'] = value
                    self.assertEqual(self.capture()[1][0]['columns'][0]['type'], ai._type(value))
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()

    def test_declared_default_catalog_type_before_admission(self):
        for catalog in (False, True, 0, 1, 1.5, '', [], ['sales'], {}, {'catalog': 'sales'}):
            with self.subTest(catalog=catalog):
                self.view('order_view', 'SELECT * FROM sales.raw.orders', defaults=(catalog, ['raw']))
                self.rejected_before_admission()
        self.view('order_view', 'SELECT 1', defaults=('FOREIGN_PRIVATE', []))
        error = self.rejected_before_admission('SQL_PERMISSION_DENIED', 403)
        self.assertNotIn('FOREIGN_PRIVATE', error)

    def test_arbitrary_external_nested_shared_cte_and_native_names(self):
        self.view('native_orders_731', 'SELECT * FROM orders')
        self.view('another_view', 'WITH orders AS (SELECT * FROM sales.raw.orders) SELECT * FROM orders')
        self.view('order_view', 'SELECT a.id FROM native_orders_731 a JOIN another_view b ON a.id=b.id')
        pins, direct, _, dependencies = self.capture('SELECT * FROM order_view JOIN orders USING(id)')
        self.assertEqual([(r['table'], r['kind']) for r in direct], [('order_view', 'VIEW'), ('orders', 'TABLE')])
        self.assertEqual([r['table'] for r in dependencies], ['another_view', 'native_orders_731'])
        self.assertEqual(len(pins), 4)
        self.assertEqual(sum(p['table'] == 'orders' for p in pins), 1)
        private = ai._json(pins)
        for secret in ('PRIVATE', 'SELECT', 's3://', 'comment', 'metadata.json'):
            self.assertNotIn(secret, private)

    def test_stored_defaults_quoted_ctes_differ_from_worksheet_and_constant(self):
        self.view('order_view', 'WITH "CTE" AS (SELECT * FROM "customers") SELECT * FROM "CTE"',
            defaults=('sales', ['marts']))
        _, _, _, dependencies = self.capture()
        self.assertEqual([(r['schema'], r['table']) for r in dependencies], [('marts', 'customers')])
        self.view('order_view', "SELECT 'PRIVATE LITERAL' AS x", defaults=None)
        parent = self.accept('SELECT * FROM order_view')
        self.assertEqual(parent['metadata']['view_dependencies'], [])
        self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')
        self.assertNotIn('PRIVATE', str(self.f.complete.call_args))

    def test_absent_null_defaults_fully_qualified_only_and_two_part_catalog(self):
        for defaults in (None, (None, ['raw'])):
            self.view('order_view', 'SELECT * FROM sales.raw.orders', defaults=defaults)
            self.assertEqual(len(self.capture()[3]), 1)
            for body in ('SELECT * FROM orders', 'SELECT * FROM raw.orders'):
                self.view('order_view', body, defaults=defaults)
                self.error('AI_PLAN_VIEW_UNSUPPORTED', self.capture)
        self.view('order_view', 'SELECT * FROM marts.customers', defaults=('sales', []))
        self.assertEqual(self.capture()[3][0]['schema'], 'marts')

    def test_cross_database_and_denied_frontier_before_child_rest_even_admin(self):
        for actor, role in ((self.principal, 'data_engineer'), (self.f.owner, 'admin')):
            context = self.f.ctx(role=role, principal=actor)
            for sql, defaults in (('SELECT * FROM finance.raw.orders', None),
                    ('SELECT 1', ('finance', ['raw'])), ('SELECT * FROM sales.hidden.orders', None)):
                self.view('order_view', sql, defaults=defaults)
                self.f.catalog_calls.clear()
                self.error('SQL_PERMISSION_DENIED', lambda: self.capture(context=context, principal=actor))
                self.assertFalse(any('/tables/orders' in path for _, path in self.f.catalog_calls))
        self.view('order_view', 'SELECT * FROM sales.raw.orders', schema='marts')
        viewer = self.f.ctx(role='bi_viewer', schema='marts')
        self.f.catalog_calls.clear()
        self.error('SQL_PERMISSION_DENIED', lambda: self.capture('SELECT * FROM order_view', context=viewer))
        self.assertFalse(any('/tables/orders' in path for _, path in self.f.catalog_calls))
        self.error('AI_INPUT_INVALID', lambda: self.capture('SELECT * FROM finance.raw.orders'))

    def test_unused_default_namespace_does_not_require_query_authority(self):
        self.view('order_view', 'SELECT * FROM sales.marts.orders', schema='marts', defaults=('sales', ['raw']))
        viewer = self.f.ctx(role='bi_viewer', schema='marts')
        self.assertEqual(self.capture('SELECT * FROM order_view', context=viewer)[3][0]['schema'], 'marts')

    def test_security_representations_schema_uuid_and_definition_boundaries(self):
        mutations = [lambda m: m.update(properties={'trino.run-as-owner': 'false'}),
            lambda m: m.update(properties={'owner': ''}),
            lambda m: m.update(properties={'trino.owner': 'PRIVATE'}),
            lambda m: m.update(properties={'trino.security': 'DEFINER'}),
            lambda m: m.update(properties=None),
            lambda m: m.update({'current-version-id': 0}),
            lambda m: m.update({'current-version-id': True}),
            lambda m: m.update({'view-uuid': 'invalid'}),
            lambda m: m['versions'][0].update(representations=[]),
            lambda m: m['versions'][0]['representations'].append(deepcopy(m['versions'][0]['representations'][0])),
            lambda m: m['versions'][0]['representations'][0].update(dialect='spark'),
            lambda m: m['schemas'].append(deepcopy(m['schemas'][0])),
            lambda m: m['schemas'][0]['fields'].append(deepcopy(m['schemas'][0]['fields'][0])),
            lambda m: m['schemas'][0]['fields'][0].pop('type'),
            lambda m: m['schemas'][0]['fields'][0].update(type='PRIVATE unsupported'),
            lambda m: m['versions'][0].update({'default-namespace': ['raw', 'nested']}),
            lambda m: m['versions'][0]['representations'][0].update(sql='SELECT ' + ' ' * 65536 + '1')]
        for change in mutations:
            metadata = self.view('order_view', 'SELECT * FROM sales.raw.orders')
            change(metadata)
            with self.subTest(change=change):
                self.error('AI_PLAN_VIEW_UNSUPPORTED', self.capture)
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()

    def test_metadata_transport_failure_has_fixed_error(self):
        self.patch('database_inventory.catalog_api', side_effect=ai.inventory.InventoryError(
            'CATALOG_UNAVAILABLE', 502, 'PRIVATE URI'))
        self.error('AI_METADATA_UNAVAILABLE', self.capture)

    def test_ineligible_view_submit_leaves_no_parent_query_or_provider(self):
        self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['owner'] = 'PRIVATE'
        self.error('AI_PLAN_VIEW_UNSUPPORTED', lambda: self.accept('SELECT * FROM order_view'))
        with store.connect() as db:
            for table in ('ai_explanations', 'query_records', 'warehouse_admissions'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        self.f.complete.assert_not_called()

    def test_platform_admin_explain_uses_actual_per_principal_database_backend(self):
        self.principal = self.f.owner
        self.context = self.f.ctx(role='admin', principal=self.principal)
        self.accept('SELECT * FROM order_view')
        result = self.run_parent()
        self.assertEqual(result['state'], 'SUCCEEDED')
        self.assertEqual(a2.qs._row(result['plan']['query_id'])['backend_username'], 'sales-owner-admin')

    def test_exact_64_relations_allowed_and_next_relation_rejected(self):
        for n in range(64):
            self.f.objects[('sales', 'raw', 't' + str(n), 'TABLE')] = self.f.metadata('t' + str(n), 'TABLE')
        body = ' UNION ALL '.join('SELECT * FROM sales.raw.t' + str(n) for n in range(63))
        self.view('order_view', body)
        self.assertEqual(len(self.capture()[0]), 64)
        self.view('order_view', body + ' UNION ALL SELECT * FROM sales.raw.t63')
        self.error('AI_PLAN_VIEW_UNSUPPORTED', self.capture)

    def test_same_version_full_head_pointer_and_child_schema_edits_are_detected(self):
        for index, change in enumerate((lambda: setattr(self, 'pointer', 's3://PRIVATE/replaced.json'),
                lambda: self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['schemas'][0]['fields'][0].update(type='string'))):
            parent = self.accept('SELECT * FROM order_view', key='head-' + str(index))
            change()
            self.error('AI_METADATA_CHANGED', lambda: plan.check_metadata(self.principal,
                self.context, parent['id'], json.loads(ai._row(parent['id'])['pins'])['relations']))
            plan.cancel(self.principal, self.context, parent['id'])

    def test_definer_or_default_change_at_terminal_preserves_known_finished(self):
        for index, change in enumerate((lambda m: m['properties'].update({'trino.owner': 'PRIVATE'}),
                lambda m: m['versions'][0].update({'default-namespace': ['marts']}),
                lambda m: m['versions'][0]['representations'][0].update(sql='SELECT * FROM sales.raw.customers'))):
            metadata = self.view('order_view', 'SELECT * FROM sales.raw.orders')
            self.accept('SELECT * FROM order_view', key='terminal-' + str(index))
            original = self.send
            def send(*args, **kwargs):
                response = original(*args, **kwargs)
                change(metadata)
                return response
            self.transport.side_effect = send
            result = self.run_parent()
            self.assertEqual((result['state'], result['plan_status'], result['plan']['query_state']),
                ('FAILED', 'FAILED', 'FINISHED'))
            self.assertEqual(result['error_code'], 'AI_METADATA_CHANGED')
            self.assertIsNone(result['plan']['operators'])
        self.f.complete.assert_not_called()

    def test_combined_public_metadata_limit(self):
        # Individually valid VIEW schemas can exceed the combined safe payload.
        for name in ('large1', 'large2'):
            metadata = self.view(name, 'SELECT 1')
            metadata['schemas'][0]['fields'] = [dict(name='col' + str(n) + 'x' * 100, type='long') for n in range(300)]
        self.view('order_view', 'SELECT * FROM large1 UNION ALL SELECT * FROM large2')
        self.error('AI_PLAN_VIEW_UNSUPPORTED', self.capture)

    def test_public_metadata_budget_excludes_timestamp_at_exact_utf8_boundary(self):
        fields = []
        for name in ('large1', 'large2'):
            metadata = self.view(name, 'SELECT 1')
            columns = [dict(name='列' + str(n), type='long') for n in range(250)]
            metadata['schemas'][0]['fields'] = columns
            fields.extend(columns)
        self.view('order_view', 'SELECT * FROM large1 UNION ALL SELECT * FROM large2')
        _, relations, _, dependencies = self.capture()
        envelope = dict(relations=relations, view_dependencies=dependencies)
        remaining = 65536 - len(ai._json(envelope).encode('utf-8'))
        self.assertGreater(remaining, 0)
        padding, extra = divmod(remaining, len(fields))
        for index, field in enumerate(fields):
            field['name'] += 'x' * (padding + (index < extra))

        _, relations, _, dependencies = self.capture()
        envelope = dict(relations=relations, view_dependencies=dependencies)
        self.assertEqual(len(ai._json(envelope).encode('utf-8')), 65536)
        self.assertGreater(len(ai._json(dict(envelope, captured_at=ai._now())).encode('utf-8')), 65536)

        fields[-1]['name'] += 'x'
        dependencies[-1]['columns'][-1]['name'] += 'x'
        self.assertEqual(len(ai._json(envelope).encode('utf-8')), 65537)
        self.rejected_before_admission()

    def test_shared_view_depth_cannot_evade_limit_through_visited_branch(self):
        self.view('a_shallow', 'SELECT * FROM shared')
        self.view('shared', 'SELECT * FROM tail')
        self.view('tail', 'SELECT 1')
        for n in range(15):
            self.view('deep' + str(n), 'SELECT * FROM ' + ('deep' + str(n + 1) if n < 14 else 'shared'))
        self.error('AI_PLAN_VIEW_UNSUPPORTED', lambda:
            self.capture('SELECT * FROM a_shallow UNION ALL SELECT * FROM deep0'))

    def test_post_gate_view_property_change_prevents_post_outside_writer_transaction(self):
        self.accept('SELECT * FROM order_view')
        @contextmanager
        def gate():
            self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['gate'] = 'PRIVATE'
            yield
        connection = Mock()
        self.transport.side_effect = self.real_transport
        with patch('warehouse_resident.warehouse_activity', gate), \
                patch.object(a2.qs.http.client, 'HTTPConnection', return_value=connection):
            result = self.run_parent()
        connection.request.assert_not_called()
        self.assert_stopped(result, 'FAILED', 'FAILED', 'AI_METADATA_CHANGED')
        self.assertIsNone(a2.qs._row(result['plan']['query_id'])['backend_query_id'])
        self.assertFalse(json.loads(ai._row(result['id'])['pins'])['control'].get('dispatch_claimed'))

    def assert_stopped(self, result, state, physical, code, *, caller=False, claimed=False):
        self.assertEqual((result['state'], result['plan_status'], result['plan']['query_state']),
            (state, state, physical))
        self.assertEqual((result['error_code'], result['plan']['error_code']), (code, code))
        self.assertEqual(result['cancel_requested'], caller)
        self.assertEqual(result['inference_state'], 'CANCELED' if caller else 'NOT_STARTED')
        self.assertIsNone(result['plan']['operators'])
        self.assertIsNone(result['text'])
        query_id = result['plan']['query_id']
        self.assertEqual(a2.qs._row(query_id)['state'], physical)
        with store.connect() as db:
            row = db.execute("SELECT * FROM warehouse_admissions WHERE subject_id=?", (query_id,)).fetchone()
            self.assertEqual(row['state'], physical)
            self.assertEqual(a2.admission.execution_unresolved(row), physical == 'UNKNOWN')
        control = json.loads(ai._row(result['id'])['pins'])['control']
        self.assertEqual(bool(control.get('cancel_claimed')), claimed)
        self.f.complete.assert_not_called()

    def test_readiness_mutation_before_first_receive_fails_without_stop_claim(self):
        self.accept('SELECT * FROM order_view')
        def readiness(principal, query_id):
            queued = self.record(self.args[2])
            self.assertEqual(queued['plan']['query_state'], 'QUEUED')
            self.assertFalse(queued['cancel_requested'])
            self.assertIsNone(a2.qs._row(query_id)['backend_query_id'])
            self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['gate'] = 'PRIVATE'
            return False
        with patch.object(a2.qs, '_ensure_query_awake', side_effect=readiness):
            result = self.run_parent()
        self.assert_stopped(result, 'FAILED', 'FAILED', 'AI_METADATA_CHANGED')
        self.assertEqual(self.calls, [])
        self.assertIsNone(a2.qs._row(result['plan']['query_id'])['backend_query_id'])
        self.assertFalse(json.loads(ai._row(result['id'])['pins'])['control'].get('dispatch_claimed'))

    def test_paginated_metadata_stop_preserves_cause_and_unknown_blocker(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                self.view('order_view', 'SELECT * FROM sales.raw.orders')
                self.accept('SELECT * FROM order_view', key='stop-' + str(unknown))
                self.pages = [dict(id='synthetic_engine', data=[],
                    nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
                def send(url, method, pin, body=None):
                    if method == 'DELETE' and unknown:
                        self.calls.append((method, body))
                        raise QueryError('QUERY_BACKEND_FAILED', 502)
                    response = self.send(url, method, pin, body)
                    if method == 'POST':
                        self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['changed'] = 'PRIVATE'
                    return response
                self.transport.side_effect = send
                start = len(self.calls)
                result = self.run_parent()
                self.assert_stopped(result, 'UNKNOWN' if unknown else 'FAILED',
                    'UNKNOWN' if unknown else 'CANCELED', 'AI_METADATA_CHANGED', claimed=True)
                query_id = result['plan']['query_id']
                a2.qs._best_cancel(query_id, cause='AI_PLAN_TIMEOUT')
                self.assertEqual(a2.qs._pins[query_id].plan.error, 'AI_METADATA_CHANGED')
                a2.qs.submit_explain(self.principal, self.context, explanation_id=result['id'])
                self.assertEqual([m for m, _ in self.calls[start:]], ['POST', 'DELETE'])
                self.assertEqual(self.record(result['id'])['error_code'], 'AI_METADATA_CHANGED')

    def test_owned_cleanup_after_sent_query_preserves_access_revocation(self):
        self.assert_owned_cleanup_revocation(unknown=False)

    def test_owned_cleanup_after_sent_query_keeps_unknown_stop(self):
        self.assert_owned_cleanup_revocation(unknown=True)

    def assert_owned_cleanup_revocation(self, *, unknown):
        parent = self.accept('SELECT * FROM order_view', key='revoked-' + str(unknown))
        self.pages = [dict(id='synthetic_engine', data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        page = a2.qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        query_id = page['id']
        self.assertIsNone(a2.qs._pins[query_id].plan.error)
        with store.connect() as db:
            grants = [dict(row) for row in db.execute(
                "SELECT * FROM grants WHERE principal_id='alice' AND database_id=?",
                (self.context.database_id,))]
            db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?",
                (self.context.database_id,))
        def send(url, method, pin, body=None):
            if method == 'DELETE' and unknown:
                self.calls.append((method, body))
                raise QueryError('QUERY_BACKEND_FAILED', 502)
            return self.send(url, method, pin, body)
        self.transport.side_effect = send
        start = len(self.calls)
        try:
            for _ in range(2):
                self.error('DB_ACCESS_REVOKED', lambda: a2.qs._owned(
                    self.principal, query_id, self.context.selected_role, cleanup=True))
            result = self.record(parent['id'])
            self.assert_stopped(result, 'UNKNOWN' if unknown else 'FAILED',
                'UNKNOWN' if unknown else 'CANCELED', 'DB_ACCESS_REVOKED', claimed=True)
            self.assertEqual(a2.qs._pins[query_id].plan.error, 'DB_ACCESS_REVOKED')
            self.assertEqual([m for m, _ in self.calls[start:]], ['DELETE'])
        finally:
            with store.connect() as db:
                for grant in grants:
                    db.execute('INSERT INTO grants (' + ','.join(grant) + ') VALUES (' +
                        ','.join('?' for _ in grant) + ')', tuple(grant.values()))

    def test_explicit_cancel_during_internal_stop_wins_without_second_delete(self):
        self.accept('SELECT * FROM order_view')
        self.pages = [dict(id='synthetic_engine', data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        def send(url, method, pin, body=None):
            response = self.send(url, method, pin, body)
            if method == 'POST':
                self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['changed'] = 'PRIVATE'
            elif method == 'DELETE':
                # A real public cancel records intent after the internal claim.
                plan.cancel(self.principal, self.context, pin.plan.explanation_id)
            return response
        self.transport.side_effect = send
        result = self.run_parent()
        self.assert_stopped(result, 'CANCELED', 'CANCELED', 'AI_PLAN_CANCELED',
            caller=True, claimed=True)
        self.assertEqual([m for m, _ in self.calls], ['POST', 'DELETE'])

    def test_internal_deadline_stop_keeps_timeout_without_caller_intent(self):
        self.accept('SELECT * FROM order_view')
        self.pages = [dict(id='synthetic_engine', data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        def send(url, method, pin, body=None):
            response = self.send(url, method, pin, body)
            if method == 'POST':
                pin.plan.deadline = 0
            return response
        self.transport.side_effect = send
        result = self.run_parent()
        self.assert_stopped(result, 'FAILED', 'CANCELED', 'AI_PLAN_TIMEOUT', claimed=True)
        self.assertEqual([m for m, _ in self.calls], ['POST', 'DELETE'])

    def test_explicit_public_query_and_registered_cancel_keep_caller_fence(self):
        for entry in ('parent', 'query', 'registered'):
            with self.subTest(entry=entry):
                parent = self.accept('SELECT * FROM order_view', key='explicit-' + entry)
                self.pages = [dict(id='synthetic_engine', data=[],
                    nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
                page = a2.qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
                def cancel():
                    if entry == 'parent':
                        return plan.cancel(self.principal, self.context, parent['id'])
                    if entry == 'query':
                        return a2.qs.cancel(self.principal, page['id'], selected_role=self.context.selected_role)
                    return a2.qs._cancel_registered(self.principal, page['id'],
                        page['next_uri'].rsplit('/', 1)[1], selected_role=self.context.selected_role)
                start = len(self.calls)
                cancel()
                cancel()
                a2.qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
                result = plan.get(self.principal, self.context, parent['id'])
                self.assert_stopped(result, 'CANCELED', 'CANCELED', 'AI_PLAN_CANCELED',
                    caller=True, claimed=True)
                self.assertEqual([m for m, _ in self.calls[start:]], ['DELETE'])

    def test_wake_dependency_change_prevents_post(self):
        parent = self.accept('SELECT * FROM order_view')
        def wake(*args):
            self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['table-uuid'] = 'replacement'
            return False
        with patch.object(a2.qs, '_ensure_query_awake', side_effect=wake):
            self.error('AI_METADATA_CHANGED', lambda: a2.qs.submit_explain(self.principal, self.context,
                explanation_id=parent['id']))
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()

    def test_view_recapture_metadata_io_never_holds_sqlite_writer(self):
        original = self.catalog
        @contextmanager
        def catalog(*args):
            with store.connect() as db:
                db.execute('PRAGMA busy_timeout=10')
                db.execute('BEGIN IMMEDIATE')
            with original(*args) as api:
                yield api
        self.transport.side_effect = self.real_transport
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [json.dumps(dict(id='synthetic_engine',
            data=[[json.dumps(a2.synthetic_plan())]])).encode(), b'']
        with patch.object(ai.inventory, 'catalog_api', catalog), \
                patch.object(a2.qs.http.client, 'HTTPConnection', return_value=connection):
            self.accept('SELECT * FROM order_view')
            self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')

    def test_change_after_valid_collection_before_model_retains_snapshot(self):
        self.accept('SELECT * FROM order_view')
        original = a2.qs.submit_explain
        def submit(*args, **kwargs):
            page = original(*args, **kwargs)
            self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['late'] = 'PRIVATE'
            return page
        with patch.object(a2.qs, 'submit_explain', side_effect=submit):
            result = self.run_parent()
        self.assertEqual((result['state'], result['plan_status']), ('FAILED', 'COLLECTED'))
        self.assertIsNotNone(result['plan']['operators'])
        self.assertIsNone(result['text'])
        self.f.complete.assert_not_called()

    def test_final_success_transaction_rechecks_every_dependency_schema(self):
        self.view('order_view', 'SELECT * FROM sales.marts.orders')
        parent = self.accept('SELECT * FROM order_view')
        original = plan.check_metadata
        def check(*args):
            original(*args)
            if self.record(parent['id'])['inference_state'] == 'RUNNING':
                with store.connect() as db:
                    db.execute("UPDATE schemas SET purpose='hidden' WHERE id=?",
                        (self.f.schemas[self.f.database.id]['marts'].id,))
        with patch.object(plan, 'check_metadata', side_effect=check):
            plan._run(*self.args)
        result = self.record(parent['id'])
        self.assertEqual((result['state'], result['plan_status'], result['error_code']),
            ('FAILED', 'COLLECTED', 'SQL_PERMISSION_DENIED'))
        self.assertIsNone(result['text'])

    def test_cancel_known_failure_and_owner_loss_keep_one_original_query(self):
        for outcome in ('cancel', 'failure', 'owner_loss'):
            with self.subTest(outcome=outcome):
                parent = self.accept('SELECT * FROM order_view', key=outcome)
                self.pages = [dict(id='synthetic_engine', data=[],
                    nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
                page = a2.qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
                if outcome == 'cancel':
                    plan.cancel(self.principal, self.context, parent['id'])
                elif outcome == 'failure':
                    self.pages = [dict(id='synthetic_engine', error=dict(message='PRIVATE'))]
                    a2.qs.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1],
                        selected_role=self.context.selected_role)
                else:
                    self.args[-1].close()
                    a2.qs._close_query_owner(a2.qs._pins[page['id']])
                result = plan.get(self.principal, self.context, parent['id'])
                self.assertIn(result['state'], ('FAILED', 'CANCELED', 'UNKNOWN'))
                self.assertEqual(self.accept('SELECT * FROM order_view', key=outcome), result)
                a2.qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        self.assertEqual(sum(m == 'POST' for m, _ in self.calls), 3)
        self.assertLessEqual(sum(m == 'DELETE' for m, _ in self.calls), 1)
        self.f.complete.assert_not_called()

    def test_provider_view_payload_contains_only_safe_dependencies_and_topology(self):
        self.frozen_view()
        _, relations, prompt, dependencies = self.capture()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [json.dumps(dict(choices=[dict(finish_reason='stop',
            message=dict(content='Only explanation'))])).encode(), b'']
        config = dict(host='127.0.0.1', port=1, model='fixture', max_tokens=64, timeout=2)
        with patch.object(ai.http.client, 'HTTPConnection', return_value=connection):
            self.assertEqual(_complete(config, prompt, relations, 'en',
                plan=plan.project(json.dumps(a2.synthetic_plan())), view_dependencies=dependencies), 'Only explanation')
        body = json.loads(connection.request.call_args.kwargs['body'])
        data = json.loads(body['messages'][1]['content'])
        self.assertEqual(set(data), {'sql', 'relations', 'plan', 'view_dependencies'})
        self.assertEqual(data['view_dependencies'], dependencies)
        self.assertIn('provenance', body['messages'][0]['content'])
        for secret in ('PRIVATE', 's3://', 'digest', 'security', 'default-catalog', 'view-uuid',
                '731234567891', 'FOR VERSION', 'snapshots'):
            self.assertNotIn(secret, json.dumps(body))

    def test_unsafe_bodies_cycles_relation_depth_and_definition_limits(self):
        for body in ('SELECT current_user', 'SELECT mystery(id) FROM orders', 'DELETE FROM orders',
                     'SELECT * FROM system.runtime.queries', 'SELECT * FROM order_view'):
            self.view('order_view', body)
            expected = 'SQL_PERMISSION_DENIED' if 'system.runtime' in body else 'AI_PLAN_VIEW_UNSUPPORTED'
            self.error(expected, self.capture)
        for index in range(17):
            self.view('v' + str(index), 'SELECT * FROM v' + str(index + 1) if index < 16 else 'SELECT 1')
        self.error('AI_PLAN_VIEW_UNSUPPORTED', lambda: self.capture('SELECT * FROM v0'))
        self.view('v15', 'SELECT 1')
        self.assertEqual(len(self.capture('SELECT * FROM v0')[0]), 16)
        self.view('order_view', ' UNION ALL '.join('SELECT * FROM sales.raw.t' + str(n) for n in range(64)))
        self.error('AI_PLAN_VIEW_UNSUPPORTED', self.capture)
        for index in range(5):
            self.view('b' + str(index), 'SELECT 1' + ' ' * 60000)
        self.view('order_view', ' UNION ALL '.join('SELECT * FROM b' + str(n) for n in range(5)))
        self.error('AI_PLAN_VIEW_UNSUPPORTED', self.capture)

    def test_table_pins_and_wire_remain_identical(self):
        expected = ai._capture(self.principal, self.context, 'SELECT * FROM orders', tables_only=True)
        actual = self.capture('SELECT * FROM orders')
        self.assertEqual(actual[:3], expected)
        self.assertIsNone(actual[3])
        result = self.accept()
        self.assertNotIn('view_dependencies', result['metadata'])
        self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')
        self.assertEqual(set(self.f.complete.call_args.kwargs), {'plan'})

    def test_original_sql_model_privacy_history_and_replay_after_delete(self):
        self.view('order_view', "SELECT * FROM sales.marts.orders WHERE amount > 567891 /*PRIVATE*/")
        parent = self.accept('SELECT * FROM order_view')
        result = self.run_parent()
        self.assertEqual(result['state'], 'SUCCEEDED')
        self.assertEqual(self.calls[0][1], b'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) SELECT * FROM order_view')
        payload = str(self.f.complete.call_args)
        self.assertIn('view_dependencies', payload)
        for secret in ('567891', 'PRIVATE', 's3://', 'digest', 'properties', 'default-catalog'):
            self.assertNotIn(secret, payload)
            self.assertNotIn(secret, ai._json(result))
        self.f.objects.clear()
        with patch.object(ai.inventory, 'catalog_api', side_effect=AssertionError('frozen history')), \
                patch.object(ai, '_config', side_effect=AssertionError('replay config')):
            self.assertEqual(plan.get(self.principal, self.context, parent['id']), result)
            self.assertEqual(plan.history(self.principal, self.context), [result])
            self.assertEqual(self.accept('SELECT * FROM order_view'), result)
        self.assertEqual(len(self.calls), 1)
        self.f.complete.assert_called_once()

    def test_full_closure_schema_rights_and_actor_bound_history(self):
        self.view('order_view', 'SELECT * FROM sales.marts.orders')
        parent = self.accept('SELECT * FROM order_view')
        self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: plan.get(self.f.bob, self.context, parent['id']))
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='hidden' WHERE id=?", (self.f.schemas[self.f.database.id]['marts'].id,))
        self.error('SQL_PERMISSION_DENIED', lambda: plan.get(self.principal, self.context, parent['id']))
        self.assertEqual(plan.history(self.principal, self.context), [])

    def test_metadata_changes_before_collection_preserve_known_finished_without_operators(self):
        original = self.send
        def send(*args, **kwargs):
            response = original(*args, **kwargs)
            self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]['properties']['changed'] = 'PRIVATE'
            return response
        self.transport.side_effect = send
        self.accept('SELECT * FROM order_view')
        result = self.run_parent()
        self.assertEqual((result['state'], result['plan']['query_state'], result['plan_status']),
            ('FAILED', 'FINISHED', 'FAILED'))
        self.assertIsNone(result['plan']['operators'])
        self.assertEqual(result['error_code'], 'AI_METADATA_CHANGED')
        self.f.complete.assert_not_called()

    def test_model_change_retains_valid_frozen_collection_discards_text(self):
        self.accept('SELECT * FROM order_view')
        def complete(*args, **kwargs):
            self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['schemas'][0]['fields'].append(dict(name='new', type='long'))
            return 'PRIVATE late explanation'
        self.f.complete.side_effect = complete
        result = self.run_parent()
        self.assertEqual((result['state'], result['plan_status'], result['plan']['query_state']),
            ('FAILED', 'COLLECTED', 'FINISHED'))
        self.assertIsNotNone(result['plan']['operators'])
        self.assertIsNone(result['text'])
        self.assertEqual(result['error_code'], 'AI_METADATA_CHANGED')

    def test_all_view_evidence_changes_recapture_and_no_duplicate_inference(self):
        for index, change in enumerate((lambda m: m['properties'].update(comment='changed'),
                lambda m: m['versions'][0].update({'default-namespace': ['marts']}),
                lambda m: m['versions'][0]['representations'][0].update(sql='SELECT * FROM sales.raw.customers'),
                lambda m: m.update({'view-uuid': str(uuid.uuid4())}))):
            metadata = self.view('order_view', 'SELECT * FROM sales.raw.orders')
            parent = self.accept('SELECT * FROM order_view', key='change-' + str(index))
            change(metadata)
            result = self.run_parent()
            self.assertEqual(result['error_code'], 'AI_METADATA_CHANGED')
            self.assertEqual(result['plan']['query_state'], 'NOT_SUBMITTED')
            self.assertEqual(self.accept('SELECT * FROM order_view', key='change-' + str(index)), result)
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()


if __name__ == '__main__':
    unittest.main()
