"""Real durable profile operations, controlled SQL transport and thread handoff."""
from dataclasses import asdict, replace
from contextlib import ExitStack
from http.client import HTTPResponse
import io
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlencode
from types import SimpleNamespace

import test_dataops_service as boundary
import test_dataops_routes as http
import control_store
import data_management as dm
import dataops_service as ds
import dataops_routes
import table_profile as profile
import query_service as qs
from query_context import QueryError

REAL_THREAD = threading.Thread


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.b = boundary.DataOpsTests()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.principal = self.b.principal
        self.context = self.b.context(schema='marts')
        self.source = self.b.existing(schema='marts')
        self.metadata = self.b.objects[('sales', 'marts', 'observed_data', 'tables')]
        self.metadata.update({'current-snapshot-id': 102, 'current-schema-id': 2,
            'snapshots': [{'snapshot-id': 101, 'schema-id': 1}, {'snapshot-id': 102, 'schema-id': 2}],
            'schemas': [{'schema-id': 1, 'fields': [dict(id=1, name='old', type='long')]},
                {'schema-id': 2, 'fields': [dict(id=1, name='名"前', type='string'),
                    dict(id=2, name='nested', type={'type': 'list', 'element': 'long'})]}]})
        self.rows = [['3', '1', '2']]
        self.posts = []
        self.after = lambda: None
        self.lost = False
        self.worker = None
        self.b.start_patch('query_service._transport', side_effect=self.transport)
        self.b.start_patch('table_profile.threading.Thread', side_effect=self.thread)

    def thread(self, *, target, args, daemon):
        self.assertIsNone(self.worker, 'one accepted thread only')
        self.worker = lambda: target(*args)
        class Held:
            def start(self):
                pass
        return Held()

    def transport(self, url, method, pin, body=None):
        if method == 'DELETE':
            return {}
        if method == 'GET':
            raise OSError('lost page')
        sql = body.decode()
        self.posts.append(sql)
        with control_store.connect() as db:
            intent = db.execute("SELECT result,committed FROM dataops_operations WHERE state='ACTIVE' AND result IS NOT NULL").fetchone()
            self.assertIn('source', json.loads(intent['result']))
            self.assertFalse(intent['committed'])
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_queries').fetchone()[0], len(self.posts))
        self.after()
        page = dict(id='backend_profile', columns=[dict(name='_p'+str(i), type='varchar')
            for i in range(len(self.rows[0]) if self.rows else 3)], data=self.rows)
        if self.lost:
            page['nextUri'] = 'http://127.0.0.1:1234/v1/statement/backend_profile/1'
        return page

    def start(self, **options):
        return profile.profile(options.pop('principal', self.principal), options.pop('context', self.context),
            key=options.pop('key', 'profile'), source=options.pop('source', self.source),
            snapshot_id=options.pop('snapshot_id', None), **options)

    def finish(self):
        work, self.worker = self.worker, None
        work()

    def observe(self, op):
        return profile.inspect(ds.Execution(self.principal, op['id']), self.context)

    def test_exact_all_columns_one_readonly_select_and_replay(self):
        op = self.start()
        self.assertEqual(op['state'], 'ACTIVE')
        self.assertFalse(self.posts)
        self.assertEqual(self.start()['id'], op['id'])
        self.finish()
        result = self.observe(op)
        self.assertEqual(result['state'], 'FINISHED')
        self.assertFalse(result['committed'])
        self.assertEqual(result['result']['row_count'], '3')
        self.assertEqual(result['result']['column_count'], '2')
        self.assertEqual([c['null_percent'] for c in result['result']['columns']], ['33.33', '66.67'])
        self.assertEqual([c['name'] for c in result['result']['columns']], ['名"前', 'nested'])
        self.assertEqual(len(self.posts), 1)
        self.assertIn('COUNT("名""前")', self.posts[0])
        self.assertIn('FOR VERSION AS OF 102', self.posts[0])
        self.assertEqual(self.start()['id'], op['id'])
        self.assertIsNone(self.worker)
        with self.assertRaises(QueryError) as conflict:
            self.start(snapshot_id='101')
        self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_owned_objects').fetchone()[0], 0)

    def assert_registered_name_profile(self, name, quoted_name):
        source = self.b.existing(name=name, schema='marts')
        key = (self.context.sql_catalog, 'marts', name, 'tables')
        metadata = self.b.objects.pop(key)
        metadata.update({k: v for k, v in self.metadata.items() if k != 'table-uuid'})
        self.b.objects[(*key[:2], quote(name, safe=''), 'tables')] = metadata
        op = self.start(source=source)
        self.finish()
        result = self.observe(op)
        self.assertEqual(result['state'], 'FINISHED')
        self.assertEqual(result['result']['row_count'], '3')
        self.assertEqual(result['result']['source']['ref'], asdict(source))
        self.assertFalse(result['committed'])
        self.assertEqual(self.posts, [
            'SELECT CAST(COUNT(*) AS VARCHAR) AS "_p0", '
            'CAST(COUNT(*)-COUNT("名""前") AS VARCHAR) AS "_p1", '
            'CAST(COUNT(*)-COUNT("nested") AS VARCHAR) AS "_p2" '
            'FROM "sales"."marts".' + quoted_name + ' FOR VERSION AS OF 102'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_queries').fetchone()[0], 1)

    def test_registered_long_physical_name(self):
        self.assert_registered_name_profile(
            'customer_transactions_archived_for_annual_analysis',
            '"customer_transactions_archived_for_annual_analysis"')

    def test_registered_uppercase_physical_name(self):
        self.assert_registered_name_profile('QuarterlySales', '"QuarterlySales"')

    def test_registered_quoted_unicode_space_name_cannot_inject_statement(self):
        self.assert_registered_name_profile(
            '売上 "集計"; DROP TABLE unrelated; --',
            '"売上 ""集計""; DROP TABLE unrelated; --"')

    def test_current_freezes_once_and_append_is_allowed(self):
        op = self.start()
        self.metadata['current-snapshot-id'] = 103
        self.metadata['snapshots'].append({'snapshot-id': 103, 'schema-id': 2})
        self.finish()
        self.assertEqual(self.observe(op)['result']['source']['snapshot'], '102')

    def test_explicit_past_uses_historic_schema(self):
        self.rows = [['3', '0']]
        op = self.start(snapshot_id='101')
        self.finish()
        result = self.observe(op)['result']
        self.assertEqual(result['columns'][0]['name'], 'old')
        self.assertEqual(result['columns'][0]['null_percent'], '0.00')
        self.assertIn('FOR VERSION AS OF 101', self.posts[0])

    def test_empty_without_snapshot_actual_count_and_default_export_unchanged(self):
        self.metadata.update({'current-snapshot-id': None, 'snapshots': []})
        self.rows = [['0', '0', '0']]
        op = self.start()
        self.finish()
        result = self.observe(op)['result']
        self.assertIsNone(result['source']['snapshot'])
        self.assertEqual(result['row_count'], '0')
        self.assertEqual([c['null_percent'] for c in result['columns']], [None, None])
        self.assertNotIn('FOR VERSION', self.posts[0])
        execution = ds.Execution(self.principal, op['id'])
        with self.assertRaises(QueryError):
            execution.export_measure(self.source)
        with self.assertRaises(QueryError):
            self.start(key='past', snapshot_id='101')

    def test_empty_with_snapshot_is_versioned(self):
        self.rows = [['0', '0', '0']]
        op = self.start()
        self.finish()
        self.assertEqual(self.observe(op)['result']['row_count'], '0')
        self.assertIn('FOR VERSION AS OF 102', self.posts[0])

    def test_metrics_precision_and_invalid_aggregates(self):
        frozen = dict(snapshot='1', columns=[dict(name='x', type='long')])
        for total in ('9007199254740993', '9223372036854775807'):
            result = profile._metrics(frozen, [[total, total]])
            self.assertEqual(result['row_count'], total)
            self.assertEqual(result['columns'][0]['null_percent'], '100.00')
        self.assertEqual(profile._metrics(frozen, [['32', '1']])['columns'][0]['null_percent'], '3.13')
        for rows in ([], [['1']], [['1', '0', '0']], [['1', '0'], ['1', '0']],
                     [[True, '0']], [['1', False]], [['01', '0']], [['-1', '0']],
                     [['1', '2']], [['9223372036854775808', '0']], [['1.0', '0']], [[' 1', '0']]):
            with self.subTest(rows=rows), self.assertRaises(QueryError):
                profile._metrics(frozen, rows)

    def test_postsql_change_never_publishes_metrics(self):
        op = self.start()
        self.after = lambda: self.metadata['schemas'][1]['fields'][0].update(name='changed')
        self.finish()
        row = ds.Execution(self.principal, op['id'])._row()
        self.assertNotIn('row_count', json.loads(row['result']))
        with self.assertRaises(QueryError):
            self.observe(op)

    def test_recreation_expiration_current_ddl_deny_observation(self):
        op = self.start()
        self.finish()
        original = json.loads(json.dumps(self.metadata))
        changes = [lambda: self.metadata.update({'table-uuid': '00000000-0000-0000-0000-000000000000'}),
            lambda: self.metadata.update(snapshots=[]),
            lambda: self.metadata['schemas'][1]['fields'][0].update(name='changed')]
        for change in changes:
            self.metadata.clear(); self.metadata.update(json.loads(json.dumps(original)))
            change()
            with self.assertRaises(QueryError):
                self.observe(op)
            with self.assertRaises(QueryError):
                self.start()
        self.assertEqual(len(self.posts), 1)

    def test_permissions_primary_schema_crossdb_and_view(self):
        for context in (self.b.context(schema='raw'), self.b.context(self.b.databases[1], schema='marts')):
            with self.assertRaises(QueryError):
                self.start(context=context, key=context.schema_id)
        viewer = self.principal
        context = self.b.context(schema='marts', principal=viewer, role='bi_viewer')
        op = self.start(principal=viewer, context=context, key='viewer')
        self.finish()
        self.assertEqual(profile.inspect(ds.Execution(viewer, op['id']), context)['state'], 'FINISHED')
        sandbox = self.b.existing('restricted', schema='sandbox')
        with self.assertRaises((QueryError, PermissionError)):
            self.start(principal=viewer, context=self.b.context(schema='sandbox', principal=viewer, role='bi_viewer'), source=sandbox, key='deny')
        view = self.b.existing('viewed', schema='marts', kind='VIEW')
        with self.assertRaises(QueryError):
            self.start(source=view, key='view')

    def test_cancel_before_sql_and_terminal(self):
        op = self.start()
        execution = ds.Execution(self.principal, op['id'])
        execution.cancel()
        self.finish()
        self.assertEqual(self.observe(op)['state'], 'CANCELED')
        self.assertFalse(self.posts)
        execution.cancel()
        self.assertEqual(self.start()['state'], 'CANCELED')

    def test_actual_async_thread_returns_id_before_sql_and_can_cancel(self):
        entered, release = threading.Event(), threading.Event()
        workers = []
        def thread(*, target, args, daemon):
            def run():
                entered.set()
                release.wait(5)
                target(*args)
            worker = REAL_THREAD(target=run, daemon=daemon)
            workers.append(worker)
            return worker
        try:
            with patch.object(profile.threading, 'Thread', side_effect=thread):
                op = self.start()
            self.assertTrue(entered.wait(2))
            self.assertEqual(op['state'], 'ACTIVE')
            self.assertFalse(self.posts)
            ds.Execution(self.principal, op['id']).cancel()
        finally:
            release.set()
            for worker in workers:
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(self.observe(op)['state'], 'CANCELED')
        self.assertFalse(self.posts)

    def test_active_query_cancel_never_publishes_metrics(self):
        self.lost = True
        op = self.start()
        def transport(url, method, pin, body=None):
            if method == 'GET':
                ds.Execution(self.principal, op['id']).cancel()
                return dict(id='backend_profile', data=[])
            return self.transport(url, method, pin, body)
        with patch.object(qs, '_transport', side_effect=transport):
            self.finish()
        observation = self.observe(op)
        self.assertEqual(observation['state'], 'CANCELED')
        self.assertNotIn('row_count', observation['result'])
        self.assertEqual(len(self.posts), 1)

    def test_restart_and_unknown_are_observation_only(self):
        op = self.start()
        # Lose worker before SQL; durable owner loss cannot authorize a resend.
        self.worker = None
        profile._slots.release()
        ds._live.pop(op['id'])[2].close()
        self.assertEqual(self.start()['state'], 'UNKNOWN')
        self.assertIsNone(self.worker)
        self.assertFalse(self.posts)

    def test_unknown_child_and_parent_never_resend_or_implicitly_cancel(self):
        self.lost = True
        op = self.start()
        self.finish()
        for state in ('ACTIVE', 'UNKNOWN'):
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET state=? WHERE id=?', (state, op['id']))
            with patch.object(qs, '_best_cancel', side_effect=AssertionError('implicit cancel')):
                replay = self.start()
            self.assertEqual(replay['state'], state)
            self.assertEqual(replay['queries'][0]['state'], 'UNKNOWN')
            self.assertNotIn('row_count', replay['result'])
        self.assertEqual(len(self.posts), 1)
        self.assertIsNone(self.worker)

    def test_revoked_grant_public_observation_precedes_inspect_autocancel(self):
        op = self.start()
        self.finish()
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        with patch.object(qs, '_best_cancel', side_effect=AssertionError('public autocancel')), \
                patch.object(ds.Execution, 'inspect', side_effect=AssertionError('untyped inspect')):
            for action in (lambda: self.observe(op), self.start):
                with self.assertRaises((QueryError, PermissionError)) as denied:
                    action()
                self.assertNotIn('row_count', str(getattr(denied.exception, 'details', {})))

    def test_midguard_and_midreplay_revocation_never_cancel_unknown_child(self):
        self.lost = True
        op = self.start()
        self.finish()
        validate = ds._validated_context
        for replay in (False, True):
            for account in (False, True):
                with self.subTest(replay=replay, account=account):
                    calls = 0
                    def revoke(principal, context):
                        nonlocal calls
                        calls += 1
                        if calls == 2:
                            raise QueryError('DB_ACCESS_REVOKED' if account else 'SQL_PERMISSION_DENIED', 403)
                        return validate(principal, context)
                    with patch.object(ds, '_validated_context', side_effect=revoke), \
                            patch.object(qs, '_best_cancel') as cancel:
                        with self.assertRaises(QueryError) as denied:
                            self.start() if replay else self.observe(op)
                        self.assertEqual(denied.exception.status, 403)
                        self.assertNotIn('operation', denied.exception.details)
                        cancel.assert_not_called()
                    self.assertEqual(len(self.posts), 1)
                    self.assertIsNone(self.worker)

    def _assert_midreplay_revocation(self, revoke):
        self.lost = True
        self.start()
        self.finish()
        begin = ds.begin
        def replay(*args, **kwargs):
            execution = begin(*args, **kwargs)
            self.assertTrue(execution.replayed)
            revoke()
            return execution
        with patch.object(ds, 'begin', side_effect=replay), patch.object(qs, '_best_cancel') as cancel:
            with self.assertRaises((QueryError, PermissionError)) as denied:
                self.start()
            self.assertNotIn('operation', getattr(denied.exception, 'details', {}))
            cancel.assert_not_called()
        self.assertEqual(len(self.posts), 1)
        self.assertIsNone(self.worker)

    def test_actual_account_revocation_between_replay_lookup_and_public_guard(self):
        def revoke():
            del self.b.fixture.users['alice']
            self.b.fixture.save()
        self._assert_midreplay_revocation(revoke)

    def test_actual_grant_revocation_between_replay_lookup_and_public_guard(self):
        def revoke():
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        self._assert_midreplay_revocation(revoke)

    def test_minimal_source_failure_and_auth_checks_on_both_sides_of_status(self):
        op = self.start()
        self.metadata['schemas'][1]['fields'][0]['name'] = 'changed'
        self.finish()
        for action in (lambda: self.observe(op), self.start):
            with self.assertRaises(QueryError) as failed:
                action()
            self.assertEqual(failed.exception.status, 409)
            self.assertEqual(failed.exception.details['operation'],
                dict(id=op['id'], state='FAILED', child_unknown=False))
        status = profile._source_status
        authorize = profile._authorize
        def revoke_after_lookup(*args):
            calls = 0
            def validate(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    with control_store.connect() as db:
                        dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
                return authorize(*args)
            with patch.object(profile, '_authorize', side_effect=validate):
                return status(*args)
        with patch.object(profile, '_source_status', side_effect=revoke_after_lookup), patch.object(qs, '_best_cancel') as cancel:
            with self.assertRaises(QueryError) as denied:
                self.observe(op)
            self.assertEqual(denied.exception.status, 403)
            self.assertNotIn('operation', denied.exception.details)
            cancel.assert_not_called()
        self.assertFalse(self.posts)

    def test_source_failure_active_without_live_owner_is_pure_readonly(self):
        self.lost = True
        op = self.start()
        self.finish()
        ds._live.pop(op['id'])[2].close()
        self.assertNotIn(op['id'], ds._live)
        self.metadata['schemas'][1]['fields'][0]['name'] = 'changed'
        for state in ('QUEUED', 'RUNNING', 'UNKNOWN', 'FINISHED', 'FAILED', 'CANCELED'):
            with self.subTest(child_state=state):
                with control_store.connect() as db:
                    db.execute("UPDATE dataops_operations SET state='ACTIVE' WHERE id=?", (op['id'],))
                    db.execute('UPDATE query_records SET state=?', (state,))
                    before = [list(map(dict, db.execute('SELECT * FROM ' + table)))
                              for table in ('dataops_operations', 'query_records', 'warehouse_admissions')]
                with patch.object(profile.ObservationExecution, '_inspect', side_effect=AssertionError('reconcile')) as inspect, \
                        patch.object(ds.Execution, 'inspect', side_effect=AssertionError('untyped inspect')), \
                        patch.object(qs, '_acquire_query_owner', side_effect=AssertionError('owner lease')) as acquire, \
                        patch.object(qs, '_best_cancel', side_effect=AssertionError('cancel')) as cancel:
                    with self.assertRaises(QueryError) as failed:
                        self.observe(op)
                    self.assertEqual(failed.exception.code, 'EXPORT_SOURCE_CHANGED')
                    self.assertEqual(failed.exception.status, 409)
                    self.assertEqual(set(failed.exception.details), {'context', 'operation'})
                    self.assertEqual(failed.exception.details['operation'],
                        dict(id=op['id'], state='ACTIVE', child_unknown=state == 'UNKNOWN'))
                    inspect.assert_not_called()
                    acquire.assert_not_called()
                    cancel.assert_not_called()
                with control_store.connect() as db:
                    after = [list(map(dict, db.execute('SELECT * FROM ' + table)))
                             for table in ('dataops_operations', 'query_records', 'warehouse_admissions')]
                self.assertEqual(after, before)
        self.assertEqual(len(self.posts), 1)

    def test_context_and_unclassified_errors_never_get_source_status_even_after_valid_recheck(self):
        op = self.start()
        self.finish()
        errors = [QueryError(code, 409) for code in ('CONTEXT_MISMATCH', 'STALE_BINDING',
            'UNKNOWN_CONTEXT', 'STALE_CONTEXT', 'DB_ACCESS_REVOKED', 'CROSS_DATABASE_TARGET',
            'PROFILE_SOURCE_CONTEXT_REQUIRED', 'UNCLASSIFIED_FAILURE')]
        errors += [QueryError('SQL_PERMISSION_DENIED', 403), PermissionError('account'),
                   RuntimeError('unclassified catalog failure')]
        for error in errors:
            with self.subTest(error=str(error)), \
                    patch.object(profile.ObservationExecution, 'export_measure', side_effect=error), \
                    patch.object(profile, '_source_status') as status:
                with self.assertRaises(type(error)) as failed:
                    self.observe(op)
                self.assertNotIn('operation', getattr(failed.exception, 'details', {}))
                status.assert_not_called()
                profile._authorize(profile.ObservationExecution(self.principal, op['id']), self.context, self.source)

    def test_normalized_catalog_source_errors_keep_http_status_without_details(self):
        op = self.start()
        self.finish()
        for code, status in (('CATALOG_UNAVAILABLE', 502), ('NOT_FOUND', 404)):
            error = profile.inventory.InventoryError(code, status, 'private catalog details')
            error.details = dict(source='private', row_count='3', backend_id='private')
            with self.subTest(code=code), \
                    patch.object(profile.ObservationExecution, 'export_measure', side_effect=error):
                with self.assertRaises(QueryError) as failed:
                    self.observe(op)
                self.assertEqual((failed.exception.code, failed.exception.status), (code, status))
                self.assertEqual(set(failed.exception.details), {'context', 'operation'})
                self.assertEqual(failed.exception.details['operation'],
                    dict(id=op['id'], state='FINISHED', child_unknown=False))
                self.assertNotIn('private', str(failed.exception.details))

    def test_capacity_and_start_failure_settle_once_release_lease_and_no_replay_thread(self):
        for busy in (True, False):
            with self.subTest(busy=busy):
                slots = threading.BoundedSemaphore(1)
                if busy:
                    slots.acquire()
                with patch.object(profile, '_slots', slots), \
                        patch.object(slots, 'release', wraps=slots.release) as released, \
                        patch.object(profile.threading, 'Thread') as thread:
                    thread.return_value.start.side_effect = RuntimeError('start failed')
                    key = 'capacity' if busy else 'start-failure'
                    with self.assertRaises((QueryError, RuntimeError)):
                        self.start(key=key)
                    with control_store.connect() as db:
                        row = dict(db.execute('SELECT * FROM dataops_operations WHERE idempotency_key=?', (key,)).fetchone())
                    self.assertEqual(row['state'], 'FAILED')
                    lease = qs._acquire_query_owner('dataops-' + row['id'])
                    lease.close()
                    self.assertEqual(self.start(key=key)['state'], 'FAILED')
                    profile._fail_definite(ds.Execution(self.principal, row['id']), self.context)
                    self.assertEqual(released.call_count, 0 if busy else 1)
                    self.assertEqual(thread.call_count, 0 if busy else 1)
                self.assertTrue(slots.acquire(blocking=False) if not busy else not slots.acquire(blocking=False))
                self.assertFalse(self.posts)

    def test_source_capture_failure_can_recover_minimal_failed_without_sql(self):
        self.metadata['snapshots'] = []
        with self.assertRaises(QueryError):
            self.start()
        with self.assertRaises(QueryError) as failed:
            self.start()
        self.assertEqual(set(failed.exception.details['operation']), {'id', 'state', 'child_unknown'})
        self.assertEqual(failed.exception.details['operation']['state'], 'FAILED')
        self.assertFalse(self.posts)
        self.assertIsNone(self.worker)

    def test_catalog_error_projection_discards_details_and_latest_auth_denial_wins(self):
        op = self.start()
        self.finish()
        def catalog_failure(*args, **kwargs):
            raise QueryError('CATALOG_UNAVAILABLE', 502, details=dict(source='private', row_count='3', backend_id='private'))
        with patch.object(profile.ObservationExecution, 'export_measure', side_effect=catalog_failure):
            with self.assertRaises(QueryError) as failed:
                self.observe(op)
            self.assertEqual(failed.exception.code, 'CATALOG_UNAVAILABLE')
            self.assertEqual(set(failed.exception.details), {'context', 'operation'})
            self.assertEqual(failed.exception.details['operation'], dict(id=op['id'], state='FINISHED', child_unknown=False))
            def run():
                raise QueryError('EXPORT_SOURCE_CHANGED', 409, details=dict(source='private'))
            with self.assertRaises(QueryError) as failed:
                dataops_routes._operation(self.principal, self.context, profile.ROUTE, 'profile', run)
            self.assertEqual(failed.exception.code, 'EXPORT_SOURCE_CHANGED')
            self.assertEqual(set(failed.exception.details), {'context', 'operation'})
        with patch.object(profile, 'inspect', side_effect=QueryError('DB_ACCESS_REVOKED', 403)):
            with self.assertRaises(QueryError) as denied:
                dataops_routes._operation(self.principal, self.context, profile.ROUTE, 'profile',
                    lambda: (_ for _ in ()).throw(failed.exception))
            self.assertEqual(denied.exception.status, 403)
            self.assertNotIn('operation', denied.exception.details)
        with patch.object(profile, 'inspect') as inspect:
            def context_failure():
                raise QueryError('CONTEXT_MISMATCH', 409)
            with self.assertRaises(QueryError) as denied:
                dataops_routes._operation(self.principal, self.context, profile.ROUTE, 'profile', context_failure)
            inspect.assert_not_called()
            self.assertNotIn('operation', denied.exception.details)

    def test_account_revocation_and_during_query_grant_loss_publish_no_metrics(self):
        op = self.start()
        def revoke():
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        self.after = revoke
        self.finish()
        self.assertNotIn('row_count', ds.Execution(self.principal, op['id'])._row()['result'])
        del self.b.fixture.users['alice']
        self.b.fixture.save()
        with self.assertRaises((QueryError, PermissionError)):
            self.observe(op)

    def test_workbench_native_handlers_post_get_cancel_and_no_unsafe_error_fallback(self):
        import request_auth
        import runtime_profile
        import warehouse_resident as wr
        handlers = []
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch.object(wr, '_profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        helper = http.HTTPRoutesTests()
        class Connection:
            def __init__(self, incoming): self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, data): self.outgoing.write(data)
        def request(path, body=None, key=None):
            payload = json.dumps(body).encode() if body is not None else b''
            raw = (('POST' if body is not None else 'GET')+' '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                'X-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload))+'\r\n'
                +('Idempotency-Key: '+key+'\r\n' if key else '')+'\r\n').encode()+payload
            connection = Connection(raw)
            with patch.object(handler, 'log_message'):
                handler(connection, ('127.0.0.1', 1), SimpleNamespace())
            response = HTTPResponse(Connection(connection.outgoing.getvalue()))
            response.begin()
            return response.status, json.loads(response.read())
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(request_auth, 'authenticate_principal', return_value=self.principal):
            # Same captured wire connection helper, invoking the actual handler class.
            original = http.wb.WorkbenchHandler
            for handler in (original, handlers[0]):
                with patch.object(helper, 'request', side_effect=request):
                    body = dict(context=asdict(self.context), source=asdict(self.source), snapshot_id=None)
                    status, accepted = helper.request('/api/v1/table-profiles', body, key='http')
                    self.assertEqual(status, 200, accepted)
                    op = accepted['operation']
                    if self.worker:
                        self.finish()
                    path = '/api/v1/dataops/' + op['id']
                    query = '?' + urlencode(dict(context=json.dumps(asdict(self.context))))
                    status, reply = helper.request(path + query)
                    self.assertEqual(status, 200, reply)
                    self.assertEqual(reply['operation']['result']['row_count'], '3')
                    status, canceled = helper.request(path+'/cancel', dict(context=asdict(self.context)))
                    self.assertEqual(status, 200, canceled)
                    self.assertEqual(set(canceled['operation']), {'id', 'state'})
            self.metadata['snapshots'] = []
            profile_body, observation_path = body, path+query
            for route, payload, key in [('/api/v1/table-profiles', profile_body, 'http'), (observation_path, None, None)]:
                status, error = helper.request(route, payload, key=key)
                self.assertEqual(status, 409, error)
                self.assertNotIn('row_count', json.dumps(error))
                minimal = error['details']['operation']
                self.assertEqual(minimal, dict(id=op['id'], state='FINISHED', child_unknown=False))
            status, error = helper.request('/api/v1/table-profiles', profile_body, key='expired-first')
            self.assertEqual(status, 409)
            first = error['details']['operation']
            self.assertEqual(first['state'], 'FAILED')
            self.assertEqual(set(first), {'id', 'state', 'child_unknown'})
            status, error = helper.request('/api/v1/table-profiles', profile_body, key='expired-first')
            self.assertEqual(status, 409)
            self.assertEqual(error['details']['operation'], first)
            status, error = helper.request('/api/v1/dataops/' + first['id'] + query)
            self.assertEqual(status, 409)
            self.assertEqual(error['details']['operation'], first)
            self.metadata['snapshots'] = [{'snapshot-id': 102, 'schema-id': 2}]
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
            with patch.object(qs, '_best_cancel', side_effect=AssertionError('public cancel')):
                for route, payload, key in [('/api/v1/table-profiles', profile_body, 'http'), (observation_path, None, None)]:
                    status, error = helper.request(route, payload, key=key)
                    self.assertEqual(status, 403, error)
                    self.assertNotIn('row_count', json.dumps(error))
                    self.assertNotIn('operation', error.get('details', {}))


if __name__ == '__main__':
    unittest.main()
