import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack, contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))


class QueryStatementWireTests(unittest.TestCase):
    def setUp(self):
        import query_service as qs
        from test_dataops_service import DataOpsTests
        self.f = DataOpsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.qs = qs

    def test_public_submit_preserves_original_and_sends_one_statement(self):
        cases = (
            ('SELECT 1;', 'SELECT 1'),
            ('SELECT 1; \n-- trailing ;\n/* ; */', 'SELECT 1 \n-- trailing ;\n/* ; */'),
            ('''SELECT 'a'';b' AS "a"";b" /* ; */; -- ;''',
             '''SELECT 'a'';b' AS "a"";b" /* ; */ -- ;'''),
            ("SELECT ';' -- ;\n", "SELECT ';' -- ;\n"),
        ) + tuple(
            ('SELECT 1; -- trailing ;' + ending + '/* ; */',
             'SELECT 1 -- trailing ;' + ending + '/* ; */')
            for ending in ('\r', '\n', '\r\n')
        )
        for original, wire in cases:
            with self.subTest(sql=original), patch.object(self.qs, '_transport',
                    return_value=dict(id='backend', data=[[1]])) as transport:
                context = self.f.context()
                page = self.qs.submit(self.f.principal, context, original, mode='read-only',
                    max_rows=10, request_id='request', worksheet_id='worksheet')
                self.assertEqual(transport.call_args.args[1], 'POST')
                self.assertEqual(transport.call_args.args[3], wire.encode())
                row = self.qs._row(page['id'])
                self.assertEqual(row['sql'], original)
                self.assertEqual((row['request_id'], row['worksheet_id']), ('request', 'worksheet'))
                self.assertEqual(row['backend_username'], transport.call_args.args[2].headers['X-Trino-User'])
                history = self.qs.history(self.f.principal, database_id=context.database_id,
                                          selected_role=context.selected_role)
                self.assertEqual(next(item['sql'] for item in history['queries'] if item['id'] == page['id']), original)

    def test_public_multiple_or_malformed_statements_denied_before_admission(self):
        import warehouse_admission as admission
        for sql in ('SELECT 1; SELECT 2', 'SELECT 1; -- ;\n SELECT 2',
                    'SELECT 1; /* ; */ SELECT 2', 'SELECT 1;;',
                    "SELECT 'unterminated;", 'SELECT 1 /* unclosed;', '-- only ;'):
            with self.subTest(sql=sql), patch.object(self.qs, '_transport') as transport, \
                    patch.object(admission, 'admit') as admit, self.assertRaises(self.qs.QueryError) as error:
                self.qs.submit(self.f.principal, self.f.context(), sql, mode='read-only',
                    max_rows=10, request_id=None, worksheet_id=None)
            self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            transport.assert_not_called()
            admit.assert_not_called()

    def test_public_second_statement_after_line_comment_denied_before_admission(self):
        import warehouse_admission as admission
        for ending in ('\r', '\n', '\r\n'):
            sql = 'SELECT 1; -- comment' + ending + 'SELECT 2'
            with self.subTest(ending=ending), patch.object(self.qs, '_transport') as transport, \
                    patch.object(admission, 'admit') as admit, self.assertRaises(self.qs.QueryError) as error:
                self.qs.submit(self.f.principal, self.f.context(), sql, mode='read-only',
                    max_rows=10, request_id=None, worksheet_id=None)
            self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            transport.assert_not_called()
            admit.assert_not_called()

    def test_private_job_and_cleanup_keep_frozen_identity_and_original_metadata(self):
        import dataops_service as ds
        import control_store
        import warehouse_resident as wr
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        original_headers = dict(frozen.headers)
        cases = (
            ('job_sql', "SELECT 'a'';b'; -- ;", "SELECT 'a'';b' -- ;"),
            ('drop_table', 'DROP TABLE "sales"."raw"."a"";b"; /* outer /* ; */ ; */',
             'DROP TABLE "sales"."raw"."a"";b" /* outer /* ; */ ; */'),
        ) + tuple(
            ('job_sql', "SELECT 'a'';b'; -- trailing ;" + ending + '/* outer /* ; */ ; */',
             "SELECT 'a'';b' -- trailing ;" + ending + '/* outer /* ; */ ; */')
            for ending in ('\r', '\n', '\r\n')
        )
        for step, (action, original, wire) in enumerate(cases):
            step = str(step)
            with self.subTest(action=action), patch.object(self.qs, '_transport',
                    return_value=dict(id='private_backend', data=[])) as transport, \
                    patch.object(wr, 'check_sql_policy', side_effect=AssertionError('public policy on generated SQL')):
                page = self.qs._submit_dataops(self.f.principal, ds._Statement(action, original),
                    frozen, parent, execution.id, step)
            self.assertEqual(transport.call_args.args[3], wire.encode())
            pin = transport.call_args.args[2]
            self.assertEqual(pin.context, frozen.context)
            self.assertIs(pin.tls, frozen.tls)
            self.assertEqual({k: v for k, v in pin.headers.items() if k != 'X-Trino-Source'},
                             {k: v for k, v in original_headers.items() if k != 'X-Trino-Source'})
            row = self.qs._row(page['id'])
            self.assertEqual(row['sql'], original)
            self.assertEqual((row['mode'], row['request_id'], row['worksheet_id']), ('dataops', execution.id, step))
            with control_store.connect() as db:
                op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
                child = db.execute('SELECT * FROM dataops_queries WHERE query_id=?', (page['id'],)).fetchone()
            self.assertEqual(row['credential_snapshot_ref'], op['credential_snapshot_ref'])
            self.assertEqual((child['operation_id'], child['step'], child['action']), (execution.id, step, action))
            expected = dict(sql=original, source=pin.headers['X-Trino-Source'],
                backend_username=pin.headers['X-Trino-User'], backend_query_id='private_backend')
            observed = dict(sql=wire, source=expected['source'], user=expected['backend_username'],
                            id='private_backend', state='FINISHED')
            self.assertTrue(self.qs._observation_matches(observed, expected))
            for key in ('sql', 'source', 'user', 'id'):
                self.assertFalse(self.qs._observation_matches(dict(observed, **{key: 'different'}), expected))
            self.assertEqual(expected['sql'], original)
        self.assertEqual(frozen.headers, original_headers)
        execution.complete()

    def test_private_multiple_statements_denied_before_child_or_admission(self):
        import dataops_service as ds
        import warehouse_admission as admission
        import control_store
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        with patch.object(self.qs, '_transport') as transport, patch.object(admission, 'admit') as admit, \
                self.assertRaises(self.qs.QueryError) as error:
            self.qs._submit_dataops(self.f.principal, ds._Statement('job_sql', 'SELECT 1; /* ; */ SELECT 2'),
                frozen, parent, execution.id, 'bad')
        self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
        transport.assert_not_called()
        admit.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries WHERE operation_id=?',
                                       (execution.id,)).fetchone()[0], 0)

    def test_private_second_statement_after_line_comment_denied_before_child_or_admission(self):
        import dataops_service as ds
        import warehouse_admission as admission
        import control_store
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        for ending in ('\r', '\n', '\r\n'):
            sql = 'SELECT 1; -- comment' + ending + 'SELECT 2'
            with self.subTest(ending=ending), patch.object(self.qs, '_transport') as transport, \
                    patch.object(admission, 'admit') as admit, self.assertRaises(self.qs.QueryError) as error:
                self.qs._submit_dataops(self.f.principal, ds._Statement('job_sql', sql),
                    frozen, parent, execution.id, 'bad')
            self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            transport.assert_not_called()
            admit.assert_not_called()
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries WHERE operation_id=?',
                                           (execution.id,)).fetchone()[0], 0)

    def test_lost_response_recovery_observes_wire_sql_keeps_original_and_requires_unique_identity(self):
        import json
        import control_store
        original, wire = 'SELECT 1; -- saved ;', 'SELECT 1 -- saved ;'
        with patch.object(self.qs, '_transport', side_effect=OSError('lost response')), \
                self.assertRaises(self.qs.QueryError):
            self.qs.submit(self.f.principal, self.f.context(), original, mode='read-only',
                max_rows=10, request_id='recover', worksheet_id='sheet')
        with control_store.connect() as db:
            query_id = db.execute('SELECT id FROM query_records').fetchone()[0]
        row = self.qs._row(query_id)
        item = dict(queryId='recovered', state='FINISHED', query=wire,
                    session=dict(source='shirokuma-query-' + query_id, user=row['backend_username']))
        for listing, state in (([item, item], 'UNKNOWN'), ([item], 'FINISHED')):
            responses = [Mock(status=200, read=Mock(return_value=json.dumps(value).encode()))
                         for value in (listing, item)]
            with patch.object(self.qs.trino_auth, 'platform_admin_credentials',
                    return_value=dict(username='mock-observer', password='mock-only')), \
                    patch.object(self.qs.http.client, 'HTTPConnection') as connection, \
                    patch.object(self.qs, '_transport') as transport, \
                    patch.object(self.qs, 'observe_query_backend', wraps=self.qs.observe_query_backend) as observe:
                connection.return_value.getresponse.side_effect = responses
                result = self.qs.reconcile_query_from_backend(self.f.principal, query_id,
                                                             selected_role='data_engineer')
            self.assertEqual(result['state'], state)
            self.assertEqual(observe.call_args.args[0]['sql'], original)
            transport.assert_not_called()
            self.assertTrue(all(call.args[0] == 'GET' for call in connection.return_value.request.call_args_list))
        recovered = self.qs._row(query_id)
        self.assertEqual((recovered['sql'], recovered['request_id'], recovered['worksheet_id']),
                         (original, 'recover', 'sheet'))
        self.assertEqual(recovered['credential_snapshot_ref'], row['credential_snapshot_ref'])


class QueryPollCleanupTests(unittest.TestCase):
    def setUp(self):
        import query_service as qs
        from test_dataops_service import DataOpsTests
        self.f = DataOpsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.qs = qs
        self.context = self.f.context()
        self.methods = []
        self.lose_page = False
        def transport(url, method, pin, body=None):
            self.assertEqual(pin.headers['Authorization'], 'Basic original-secret')
            self.methods.append(method)
            if method == 'DELETE':
                return {}
            if method == 'GET' and self.lose_page:
                raise OSError('lost page')
            return dict(id='poll', data=[[1]], nextUri='http://127.0.0.1:1234/v1/statement/poll/1')
        patcher = patch.object(qs, '_transport', side_effect=transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = qs.submit(self.f.principal, self.context, 'SELECT 1', mode='read-only',
                              max_rows=100, request_id=None, worksheet_id=None)
        self.token = self.page['next_uri'].rsplit('/', 1)[1]

    def poll_denied(self):
        with self.assertRaises(self.qs.QueryError) as denied:
            self.qs.fetch(self.f.principal, self.page['id'], self.token,
                          selected_role=self.context.selected_role)
        self.assertEqual((denied.exception.status, denied.exception.details), (403, {}))

    def test_retiring_unknown_poll_stays_blocking_until_explicit_original_pin_cancel(self):
        import control_store
        import warehouse_admission as admission
        self.lose_page = True
        with self.assertRaises(self.qs.QueryError):
            self.qs.fetch(self.f.principal, self.page['id'], self.token,
                          selected_role=self.context.selected_role)
        self.f.retire()
        self.poll_denied()
        self.assertEqual(self.methods.count('DELETE'), 0)
        self.assertEqual(self.qs._row(self.page['id'])['state'], 'UNKNOWN')
        with control_store.connect() as db:
            self.assertEqual([r['state'] for r in admission.blockers(db, 'w')], ['UNKNOWN'])
        canceled = self.qs.cancel(self.f.principal, self.page['id'], selected_role=self.context.selected_role)
        self.assertEqual(canceled, dict(id=self.page['id'], state='CANCELED'))
        self.assertEqual(self.methods.count('DELETE'), 1)

    def assert_cleanup(self, sql=None, params=(), account=None, state='retiring'):
        import control_store
        self.f.retire(state)
        if state == 'retiring':
            self.poll_denied()
            self.assertEqual(self.methods.count('DELETE'), 0)
        if sql:
            with control_store.connect() as db:
                db.execute(sql, params)
        if account == 'remove':
            del self.f.fixture.users['alice']
            self.f.fixture.save()
        elif account == 'role':
            self.f.fixture.users['alice']['role'] = 'bi_viewer'
            self.f.fixture.save()
        self.poll_denied()
        self.assertEqual(self.methods.count('DELETE'), 1)
        self.assertEqual(self.qs._row(self.page['id'])['state'], 'CANCELED')

    def test_retiring_grant_loss_still_cleans_up(self):
        self.assert_cleanup('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                            ('alice', self.context.database_id))

    def test_retiring_account_loss_still_cleans_up(self):
        self.assert_cleanup(account='remove')

    def test_retiring_global_role_loss_still_cleans_up(self):
        self.assert_cleanup(account='role')

    def test_retiring_binding_loss_still_cleans_up(self):
        self.assert_cleanup('UPDATE catalog_bindings SET desired_revision=desired_revision+1 WHERE database_id=?',
                            (self.context.database_id,))

    def test_retiring_generation_loss_still_cleans_up(self):
        self.assert_cleanup('UPDATE catalog_bindings SET applied_generation_id=? WHERE database_id=?',
                            ('g-' + self.f.databases[1], self.context.database_id))

    def test_retiring_epoch_loss_still_cleans_up(self):
        self.assert_cleanup('UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id=? AND database_id=?',
                            ('alice', self.context.database_id))

    def test_retiring_schema_loss_still_cleans_up(self):
        self.assert_cleanup('UPDATE schemas SET sql_name=? WHERE id=?', ('renamed', self.context.schema_id))

    def test_retiring_schema_role_loss_still_cleans_up(self):
        self.assert_cleanup('UPDATE schemas SET purpose=? WHERE id=?', ('custom', self.context.schema_id))

    def test_retiring_warehouse_loss_still_cleans_up(self):
        from dataclasses import replace
        import control_store
        with control_store.connect() as db:
            generation = self.qs.registry.get_generation(db, self.context.generation_id)
        self.f.retire()
        with patch.object(self.qs.registry, 'get_generation', return_value=replace(generation, warehouse_id='different')):
            self.poll_denied()
        self.assertEqual(self.methods.count('DELETE'), 1)

    def test_retiring_catalog_loss_still_cleans_up(self):
        self.assert_cleanup('UPDATE databases SET sql_catalog=? WHERE id=?', ('different', self.context.database_id))

    def test_retired_never_suppresses_cleanup(self):
        self.assert_cleanup(state='retired')


class QueryOriginLivenessTests(unittest.TestCase):
    def setUp(self):
        import query_service as qs
        import warehouse_resident as wr
        from test_dataops_service import DataOpsTests
        persistent_trino = wr.persistent_trino
        self.f = DataOpsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.qs, self.wr = qs, wr
        self.old_origin = 'http://127.0.0.1:1234'
        self.new_origin = 'http://127.0.0.1:5678'
        self.old_process, self.new_process = Mock(), Mock()
        self.old_process.poll.return_value = None
        self.new_process.poll.return_value = None
        self.opened = []
        @contextmanager
        def connection():
            self.opened.append(self.new_origin)
            yield dict(url=self.new_origin, process=self.new_process, kubectl=wr.KUBECTL)
        lifetime = ExitStack()
        for patcher in (
            patch.object(wr, 'persistent_trino', persistent_trino),
            patch.object(wr, '_trino_connection', connection),
            patch.object(wr, '_sql_kubectl', return_value=wr.KUBECTL),
            patch.object(wr, '_PERSISTENT_TRINO',
                         dict(url=self.old_origin, process=self.old_process, kubectl=wr.KUBECTL)),
            patch.object(qs, '_lifetime', lifetime),
            patch.object(qs, '_origin', self.old_origin),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lifetime.close)

    def submit(self, sql='SELECT 1', request_id='first'):
        return self.qs.submit(self.f.principal, self.f.context(), sql, mode='read-only',
                              max_rows=100, request_id=request_id, worksheet_id='worksheet')

    def running_page(self):
        return dict(id='old_backend', nextUri=self.old_origin + '/v1/statement/old_backend/1')

    def make_unknown(self):
        with patch.object(self.qs, '_transport', return_value=self.running_page()):
            page = self.submit()
        token = page['next_uri'].rsplit('/', 1)[1]
        with patch.object(self.qs, '_transport', side_effect=OSError('lost page')):
            with self.assertRaises(self.qs.QueryError):
                self.qs.fetch(self.f.principal, page['id'], token, selected_role='data_engineer')
        self.assertEqual(self.qs._row(page['id'])['state'], 'UNKNOWN')
        return page

    def test_dead_cached_origin_renews_only_for_distinct_new_request(self):
        from copy import deepcopy
        qs = self.qs
        page = self.make_unknown()
        pin = qs._pins[page['id']]
        row = qs._row(page['id'])
        snapshot = deepcopy((pin.origin, pin.headers, pin.tokens, pin.cancel_urls, pin.responses))
        context, tls = pin.context, pin.tls
        self.old_process.poll.return_value = 1
        with patch.object(qs, '_transport', return_value=dict(id='new_backend', data=[[2]])) as transport:
            new = self.submit('SELECT 2', 'distinct-request')
        self.assertEqual(transport.call_args.args[0], self.new_origin + '/v1/statement')
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.call_args.args[1], 'POST')
        self.assertEqual(transport.call_args.args[3], b'SELECT 2')
        self.assertEqual(self.opened, [self.new_origin])
        self.assertNotEqual(new['id'], page['id'])
        self.assertEqual(qs._row(new['id'])['request_id'], 'distinct-request')
        self.assertEqual(qs._row(page['id']), row)
        self.assertIs(qs._pins[page['id']], pin)
        self.assertIs(pin.context, context)
        self.assertIs(pin.tls, tls)
        self.assertEqual((pin.origin, pin.headers, pin.tokens, pin.cancel_urls, pin.responses), snapshot)

    def test_live_cached_origin_is_reused_without_new_owner(self):
        with patch.object(self.qs, '_transport', return_value=dict(id='live_backend', data=[])) as transport:
            self.submit()
            self.submit('SELECT 2', 'second')
        self.assertEqual(self.opened, [])
        self.assertEqual([call.args[0] for call in transport.call_args_list],
                         [self.old_origin + '/v1/statement'] * 2)

    def test_cached_origin_with_changed_configuration_gets_new_owner(self):
        with patch.object(self.wr, '_sql_kubectl', return_value=['different', 'kubectl']), \
                patch.object(self.qs, '_transport', return_value=dict(id='configured', data=[])) as transport:
            self.submit()
        self.assertEqual(transport.call_args.args[0], self.new_origin + '/v1/statement')
        self.assertEqual(self.opened, [self.new_origin])

    def test_dataops_empty_origin_renews_but_existing_frozen_origin_is_retained(self):
        import dataops_service as ds
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        self.assertEqual(frozen.origin, '')
        context, headers, tls = frozen.context, dict(frozen.headers), frozen.tls
        self.old_process.poll.return_value = 1
        with patch.object(self.qs, '_transport', return_value=dict(id='child', data=[])) as transport:
            first = self.qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 1'),
                                            frozen, parent, execution.id, 'first')
            self.assertEqual(frozen.origin, self.new_origin)
            self.new_process.poll.return_value = 1
            self.qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 2'),
                                    frozen, parent, execution.id, 'second')
            with self.assertRaises(self.qs.QueryError) as duplicate:
                self.qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 1'),
                                        frozen, parent, execution.id, 'first')
        self.assertEqual(duplicate.exception.code, 'STEP_ALREADY_SUBMITTED')
        self.assertEqual(self.opened, [self.new_origin])
        self.assertEqual([call.args[0] for call in transport.call_args_list],
                         [self.new_origin + '/v1/statement'] * 2)
        self.assertIs(frozen.context, context)
        self.assertEqual(frozen.headers, headers)
        self.assertIs(frozen.tls, tls)
        self.assertEqual(self.qs._pins[first['id']].origin, self.new_origin)

    def test_observer_renews_dead_origin_get_only_and_absence_keeps_old_unknown(self):
        from copy import deepcopy
        qs = self.qs
        page = self.make_unknown()
        pin = qs._pins[page['id']]
        row = qs._row(page['id'])
        snapshot = deepcopy((pin.origin, pin.headers, pin.tokens, pin.cancel_urls, pin.responses))
        self.old_process.poll.return_value = 1
        credentials = dict(platform_admin=dict(username='observer', password='private'))
        with patch.object(qs.trino_auth, 'load_database_credentials', return_value=credentials), \
                patch.object(qs.http.client, 'HTTPConnection') as connection, \
                patch.object(qs, '_transport') as transport:
            connection.return_value.getresponse.return_value.status = 404
            result = qs.reconcile_query_from_backend(self.f.principal, page['id'], selected_role='data_engineer')
        self.assertEqual(connection.call_args.args, ('127.0.0.1', 5678))
        self.assertEqual(self.opened, [self.new_origin])
        self.assertEqual(connection.return_value.request.call_count, 1)
        self.assertEqual(connection.return_value.request.call_args.args,
                         ('GET', '/v1/query/old_backend'))
        transport.assert_not_called()
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(qs._row(page['id']), row)
        self.assertIs(qs._pins[page['id']], pin)
        self.assertEqual((pin.origin, pin.headers, pin.tokens, pin.cancel_urls, pin.responses), snapshot)


class QueryContractTests(unittest.TestCase):
    def test_context_is_frozen(self):
        import query_context
        context = query_context.ResolvedQueryContext('db', 'schema', 'LOCAL', 'lake', 'marts', 'bi_viewer', 1, 'gen', 1)
        with self.assertRaises(AttributeError):
            context.sql_catalog = 'other'

    def test_service_contract(self):
        import query_service
        for name in ('submit', 'fetch', 'cancel', 'history', 'inspect_query'):
            self.assertTrue(callable(getattr(query_service, name)))
        error = query_service.QueryError('QUERY_CONTEXT_LOST', 409)
        self.assertEqual(error.status, 409)


class QueryHistoryTests(unittest.TestCase):

    def test_retirement_after_root_admission_commits_runs_once_but_denies_response(self):
        import control_store
        import query_service as qs
        import warehouse_admission as admission
        from test_dataops_service import DataOpsTests
        fixture = DataOpsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.context(schema='marts', role='bi_viewer')
        accepted = admission.accepted
        retired = []
        @contextmanager
        def retire_after_commit(handle, **kwargs):
            if not retired:
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE id=?',
                                                (handle.id,)).fetchone()[0], 'ACTIVE')
                fixture.retire()
                retired.append(True)
            with accepted(handle, **kwargs):
                yield handle
        with patch.object(admission, 'accepted', side_effect=retire_after_commit), \
                patch.object(qs, '_transport', return_value=dict(id='root_once', data=[[123]])) as transport:
            with self.assertRaises(qs.QueryError):
                qs.submit(fixture.principal, context, 'SELECT 123', mode='read-only',
                          max_rows=10, request_id=None, worksheet_id=None)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.call_args.args[1], 'POST')
        self.assertEqual(transport.call_args.args[2].headers['Authorization'], 'Basic original-secret')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM query_records').fetchone()[0], 'FINISHED')
            self.assertEqual(db.execute('SELECT state FROM warehouse_admissions').fetchone()[0], 'FINISHED')
        fixture.assert_database_admissions()

    def test_retiring_root_query_read_page_history_denied_original_pin_cancel_allowed(self):
        import control_store
        import query_service as qs
        from test_dataops_service import DataOpsTests
        fixture = DataOpsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.context(schema='marts', role='bi_viewer')
        deletes = []
        def transport(url, method, pin, body=None):
            if method == 'DELETE':
                deletes.append(pin.headers['Authorization'])
                return {}
            return dict(id='root', data=[[1]], nextUri='http://127.0.0.1:1234/v1/statement/root/1')
        with patch.object(qs, '_transport', side_effect=transport):
            result = qs.submit(fixture.principal, context, 'SELECT 1', mode='read-only',
                               max_rows=10, request_id=None, worksheet_id=None)
            fixture.retire()
            for read in (
                lambda: qs.inspect_query(fixture.principal, result['id'], selected_role='bi_viewer'),
                lambda: qs.fetch(fixture.principal, result['id'], result['next_uri'].rsplit('/', 1)[1],
                                 selected_role='bi_viewer'),
                lambda: qs.history(fixture.principal, database_id=context.database_id, selected_role='bi_viewer')):
                with self.assertRaises((qs.QueryError, PermissionError)):
                    read()
            canceled = qs.cancel(fixture.principal, result['id'], selected_role='bi_viewer')
        self.assertEqual(set(canceled), {'id', 'state'})
        self.assertEqual(deletes, ['Basic original-secret'])
        fixture.assert_database_admissions()

    def test_history_omits_stale_dataops_records_without_rejecting_current_database(self):
        from test_dataops_service import DataOpsTests
        import control_store
        import data_management as dm
        import query_service as qs
        fixture = DataOpsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        old = fixture.begin('old')
        ref = fixture.existing()
        old_query = old.select(ref, ['id'], step='old-read')['id']
        old.complete()
        context = fixture.context()
        with control_store.connect() as db:
            desired = dm.get_grant(db, fixture.principal.principal_id, context.database_id)
            dm.update_grant(db, fixture.principal.principal_id, context.database_id,
                            desired.revision, 'data_engineer')
        fresh = fixture.begin('fresh', context=fixture.context())
        fresh_query = fresh.select(ref, ['id'], step='fresh-read')['id']
        fresh.complete()
        with patch.object(qs, '_transport', return_value=dict(id='viewer', data=[[1]])):
            viewer = qs.submit(fixture.principal, fixture.context(schema='marts', role='bi_viewer'),
                'SELECT 1', mode='read-only', max_rows=10, request_id='other-role', worksheet_id=None)
        with self.assertRaises(qs.QueryError) as error:
            qs.inspect_query(fixture.principal, old_query, selected_role='data_engineer')
        self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
        with patch.object(qs, '_transport') as transport:
            history = qs.history(fixture.principal, database_id=context.database_id,
                                 selected_role='data_engineer')
        self.assertEqual([item['id'] for item in history['queries']], [fresh_query])
        transport.assert_not_called()
        self.assertEqual([item['id'] for item in qs.history(fixture.principal,
            database_id=context.database_id, selected_role='bi_viewer')['queries']], [viewer['id']])
        with patch.object(qs, 'inspect_query', side_effect=qs.QueryError('QUERY_BACKEND_FAILED', 502)), \
                self.assertRaises(qs.QueryError) as error:
            qs.history(fixture.principal, database_id=context.database_id, selected_role='data_engineer')
        self.assertEqual(error.exception.code, 'QUERY_BACKEND_FAILED')
        inspect = qs.inspect_query
        def revoke_after_record(*args, **kwargs):
            item = inspect(*args, **kwargs)
            with control_store.connect() as db:
                db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                           (fixture.principal.principal_id, context.database_id))
            return item
        with patch.object(qs, 'inspect_query', side_effect=revoke_after_record), \
                patch.object(qs, '_transport') as transport, self.assertRaises(qs.QueryError) as error:
            qs.history(fixture.principal, database_id=context.database_id, selected_role='data_engineer')
        self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
        transport.assert_not_called()
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                       (fixture.principal.principal_id, context.database_id))
        with self.assertRaises(qs.QueryError) as error:
            qs.history(fixture.principal, database_id=context.database_id, selected_role='data_engineer')
        self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')


class QueryExecutionTests(unittest.TestCase):
    def test_submit_propagates_current_platform_decision_and_scoped_admin_identity(self):
        import base64
        import json
        import os
        import control_store
        import data_management as dm
        import query_service as qs
        import trino_auth
        import warehouse_resident as wr
        self.fixture.users['alice']['role'] = 'admin'
        self.fixture.save()
        self.principal = self.fixture.principal()
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.fixture.sales, 1, 'admin')
            # Synthetic effective authority isolates submission/policy, not apply.
            db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1 WHERE principal_id='alice'")
            dm.create_schema(db, self.fixture.finance, 'raw', 'Raw', 'raw', 'test')
        config = dict(version=2, platform_admin=dict(username='shirokuma_admin', password='platform-password'),
            databases={key: dict(sql_catalog=catalog, generation_id='g-'+key,
                roles={role: dict(username=trino_auth.scoped_username(key, role), password=key+role)
                       for role in ('bi_viewer', 'data_engineer', 'admin')})
                for key, catalog in ((self.fixture.sales, 'sales'), (self.fixture.finance, 'finance'))})
        path = self.fixture.root/'admin-backend.json'
        def save():
            path.write_text(json.dumps(config))
            path.chmod(0o600)
        save()
        @contextmanager
        def backend():
            yield 'https://trusted:8443'
        def submit(sql):
            return qs.submit(self.principal, self.context('raw', 'admin'), sql,
                mode='admin-write', max_rows=10, request_id=None, worksheet_id=None)
        with patch.dict(os.environ, SHIROKUMA_TRINO_AUTH_MODE='password', SHIROKUMA_TRINO_AUTH_FILE=str(path)), \
             patch.object(trino_auth, 'backend_ssl_context', return_value=None), \
             patch.object(wr, 'persistent_trino', backend), \
             patch.object(qs, '_transport', return_value=dict(id='backend', data=[])) as transport, \
             patch.object(wr, 'check_sql_policy', wraps=wr.check_sql_policy) as policy:
            submit('INSERT INTO sales.raw.t VALUES (1)')
            self.assertIs(policy.call_args.kwargs['_platform_authorized'], False)
            pin = transport.call_args.args[2]
            user = trino_auth.scoped_username(self.fixture.sales, 'admin')
            self.assertEqual(pin.headers['X-Trino-User'], user)
            self.assertEqual(base64.b64decode(pin.headers['Authorization'].split()[1]).decode(),
                user+':'+config['databases'][self.fixture.sales]['roles']['admin']['password'])
            for sql in ('INSERT INTO finance.raw.t VALUES (1)', 'SELECT * FROM system.runtime.queries',
                        'SHOW SESSION', 'SHOW CATALOGS', 'SET SESSION query_max_run_time=\'1h\''):
                transport.reset_mock()
                with self.subTest(sql=sql), self.assertRaises(qs.QueryError) as error:
                    submit(sql)
                self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
                transport.assert_not_called()
            self.principal = self.fixture.principal('owner')
            submit('INSERT INTO finance.raw.t VALUES (1)')
            self.assertIs(policy.call_args.kwargs['_platform_authorized'], True)
            self.assertEqual(transport.call_args.args[2].headers['X-Trino-User'], 'shirokuma_admin')
            # A formerly platform principal object cannot preserve authority after
            # the CURRENT account becomes an ordinary database administrator.
            self.fixture.users['owner']['platform_admin'] = False
            self.fixture.save()
            with control_store.connect() as db:
                dm.create_grant(db, 'owner', self.fixture.sales, 'admin')
                db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1 WHERE principal_id='owner'")
            transport.reset_mock()
            with self.assertRaises(qs.QueryError) as error:
                submit('INSERT INTO finance.raw.t VALUES (1)')
            self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            self.assertIs(policy.call_args.kwargs['_platform_authorized'], False)
            transport.assert_not_called()
            config['databases'][self.fixture.sales]['roles'].pop('admin')
            save()
            policy.reset_mock()
            with self.assertRaises(qs.QueryError) as error:
                submit('SELECT 1')
            self.assertEqual(error.exception.code, 'FEATURE_UNAVAILABLE')
            policy.assert_not_called()
            transport.assert_not_called()

    def test_null_epoch_and_role_name_never_authorize_platform_sql(self):
        from dataclasses import replace
        import warehouse_resident as wr
        context = replace(self.context('raw', 'data_engineer'), selected_role='admin', grant_revision=None)
        for authority in (False, None, 1, 'true'):
            for sql in ('SELECT * FROM finance.raw.t', 'SHOW SESSION', 'SHOW CATALOGS'):
                with self.subTest(authority=authority, sql=sql), self.assertRaises(ValueError):
                    wr.check_sql_policy(sql, 'admin-write', 'admin', context=context,
                        _platform_authorized=authority)

    def setUp(self):
        from test_request_auth import PrincipalTests
        self.fixture = PrincipalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        import control_store
        import data_management as dm
        with control_store.connect() as db:
            self.schemas = {name: dm.create_schema(db, self.fixture.sales, name, name, name, 'test').id
                            for name in ('raw', 'sandbox', 'staging', 'marts')}
        self.principal = self.fixture.principal()
        import query_service as qs
        for pin in list(qs._pins.values()):
            qs._close_query_owner(pin)
        qs._pins.clear()
        self.addCleanup(lambda: [qs._close_query_owner(pin) for pin in list(qs._pins.values())])
        qs._origin = None

    def context(self, schema='marts', role='bi_viewer', **kwargs):
        import query_context
        return query_context.resolve_context(self.principal, self.fixture.sales, self.schemas[schema], role,
            warehouse_id='w', expected_binding_revision=kwargs.get('revision', 1))

    def test_context_rejects_stale_cross_parent_and_forbidden_schema(self):
        import query_context as qc
        for call, code in ((lambda: self.context(revision=2), 'STALE_BINDING'),
                           (lambda: self.context('raw'), 'SQL_PERMISSION_DENIED'),
                           (lambda: qc.resolve_context(self.principal, self.fixture.finance,
                               self.schemas['marts'], 'bi_viewer', warehouse_id='w', expected_binding_revision=1), 'UNKNOWN_CONTEXT')):
            with self.assertRaises(qc.QueryError) as error:
                call()
            self.assertEqual(error.exception.code, code)

    def test_native_names_and_sql_unchanged(self):
        import warehouse_resident as wr
        ctx = self.context('raw', 'data_engineer')
        for sql in ('SELECT * FROM events', 'SELECT * FROM raw.events', 'SELECT * FROM sales.raw.events',
                    'INSERT INTO events VALUES (1)', 'INSERT INTO raw.events VALUES (1)',
                    'INSERT INTO sales.raw.events VALUES (1)'):
            self.assertEqual(wr.check_sql_policy(sql, 'sandbox-write', 'data_engineer', context=ctx), sql)
        for sql in ('SELECT * FROM finance.raw.events', 'INSERT INTO staging.events VALUES (1)',
                    'DROP TABLE events'):
            with self.assertRaises(ValueError):
                wr.check_sql_policy(sql, 'sandbox-write', 'data_engineer', context=ctx)
        self.assertIn('_qualify_sub', wr.normalize_trino_sql('SELECT x FROM iceberg.marts.t QUALIFY x > 0'))

    def test_admin_registered_catalog_and_custom_schema_writes(self):
        import warehouse_resident as wr
        import query_context
        import control_store
        import data_management as dm
        with control_store.connect() as db:
            dm.create_schema(db, self.fixture.finance, 'custom', 'custom', '', 'test')
            custom = dm.create_schema(db, self.fixture.sales, 'custom', 'custom', '', 'test')
        principal = self.fixture.principal('owner')
        ctx = query_context.resolve_context(principal, self.fixture.sales, custom.id, 'admin',
                                           warehouse_id='w', expected_binding_revision=1)
        import request_auth
        decision = request_auth.authorize_database(principal, ctx.database_id, 'admin',
            expected_generation_id=ctx.generation_id)
        for sql in ('INSERT INTO finance.custom.events VALUES (1)', 'INSERT INTO custom.events VALUES (1)',
                    'INSERT INTO events VALUES (1)', 'CREATE TABLE finance.custom.new_table AS SELECT 1'):
            self.assertEqual(wr.check_sql_policy(sql, 'admin-write', 'admin', context=ctx,
                _platform_authorized=decision.platform_admin), sql)
        for sql in ('INSERT INTO missing.custom.events VALUES (1)', 'INSERT INTO finance.missing.events VALUES (1)'):
            with self.assertRaises(ValueError):
                wr.check_sql_policy(sql, 'admin-write', 'admin', context=ctx,
                    _platform_authorized=decision.platform_admin)

    def test_cli_uses_authenticated_endpoint_and_refuses_redirects(self):
        import json
        import io
        from types import SimpleNamespace
        import warehouse_resident as wr
        path = self.fixture.root / 'client.json'
        path.write_text(json.dumps({'username': 'alice', 'password': 'correct', 'selected_role': 'bi_viewer'}))
        path.chmod(0o600)
        profile = SimpleNamespace(client_auth_file=str(path), sql_endpoint_url='https://public')
        opener = unittest.mock.MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"id":"q","data":[[1]]}')
        handlers = []
        def build(handler):
            handlers.append(handler)
            return opener
        with patch('runtime_profile.current', return_value=profile), patch.object(wr.urllib.request, 'build_opener', side_effect=build):
            self.assertEqual(wr._profile_client_query('SELECT 1', 'sales', 'marts', 10)['rows'], [[1]])
            request = opener.open.call_args.args[0]
            self.assertTrue(request.get_header('Authorization').startswith('Basic '))
            self.assertEqual(request.get_header('X-shirokuma-mode'), 'read-only')
            self.assertEqual(request.get_header('X-shirokuma-mode'), 'read-only')
            self.assertEqual(request.full_url, 'https://public/v1/statement')
            self.assertIsNone(handlers[0].redirect_request(None, None, 302, '', {}, 'https://evil'))
            for maximum in (None, -1, 0):
                count = opener.open.call_count
                with self.assertRaises(ValueError):
                    wr._profile_client_query('SELECT 1', 'sales', 'marts', maximum)
                self.assertEqual(opener.open.call_count, count)
            opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"id":"q","nextUri":"https://evil/v1/statement/app/pages/token"}')
            with self.assertRaises(ValueError):
                wr._profile_client_query('SELECT 1', 'sales', 'marts', 10)

    def test_native_dispatch_auth_and_exact_routes(self):
        import warehouse_resident as wr
        import query_service as qs
        from email.message import Message
        headers = self.fixture.headers()
        headers['X-Shirokuma-Role'] = 'bi_viewer'
        headers['X-Trino-Catalog'] = 'sales'
        headers['X-Trino-Schema'] = 'marts'
        with patch.object(qs, 'submit', return_value={'id': 'app', 'backend_query_id': 'backend',
             'columns': [], 'data': [], 'stats': {}, 'state': 'RUNNING',
             'next_uri': '/api/v1/queries/app/pages/opaque', 'cancel_uri': '/api/v1/queries/app/cancel'}):
            page = wr._native_request(headers, '/v1/statement', 'POST', b'SELECT * FROM events', 'https://public', warehouse_id='w')
            self.assertEqual(page['id'], 'backend')
            self.assertEqual(page['nextUri'], 'https://public/v1/statement/app/pages/opaque')
        for path, method in (('/v1/statement?x=1', 'POST'), ('/v1/info', 'POST'),
                             ('/v1/statement/app/cancel', 'GET')):
            with self.assertRaises(qs.QueryError):
                wr._native_request(headers, path, method, b'', 'https://public')
        headers['X-Trino-Session'] = 'foo=bar'
        with self.assertRaises(qs.QueryError):
            wr._native_request(headers, '/v1/statement', 'POST', b'SELECT 1', 'https://public')
        del headers['X-Trino-Session']
        for name in ('X-Shirokuma-Role', 'X-Trino-Catalog', 'X-Trino-Schema'):
            duplicate = Message()
            for key, value in headers.items():
                duplicate[key] = value
            duplicate[name] = headers[name]
            with self.assertRaises(qs.QueryError) as error:
                wr._native_request(duplicate, '/v1/statement', 'POST', b'SELECT 1', 'https://public')
            self.assertEqual(error.exception.status, 400)

    def test_real_credentials_rotated_after_submit_remain_pinned(self):
        import json
        import os
        import base64
        import trino_auth
        import query_service as qs
        import request_auth
        config = {'version': 2, 'platform_admin': {'username': 'shirokuma_admin', 'password': 'admin-secret'},
            'databases': {self.fixture.sales: {'sql_catalog': 'sales', 'generation_id': 'g-' + self.fixture.sales,
                'roles': {role: {'username': trino_auth.scoped_username(self.fixture.sales, role), 'password': role + '-old'}
                          for role in ('bi_viewer', 'data_engineer')}}}}
        path = self.fixture.root / 'trino.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        @contextmanager
        def backend():
            yield 'https://trusted:8443'
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(pin.headers['Authorization'])
            return {'id': 'q', 'nextUri': 'https://trusted:8443/v1/statement/executing/q/0/a'} if method == 'POST' else {'id': 'q'}
        ctx = self.context()
        original_authorize = request_auth.authorize_database
        with patch.dict(os.environ, SHIROKUMA_TRINO_AUTH_MODE='password', SHIROKUMA_TRINO_AUTH_FILE=str(path)), \
             patch('trino_auth.backend_ssl_context', return_value=None), \
             patch('warehouse_resident.persistent_trino', backend), patch.object(qs, '_transport', side_effect=transport), \
             patch('request_auth.authorize_database', wraps=original_authorize) as authorize:
            page = qs.submit(self.principal, ctx, 'SELECT 1', mode='read-only', max_rows=10, request_id=None, worksheet_id=None)
            self.assertEqual(authorize.call_count, 4)
            self.assertEqual(sum('db' in call.kwargs for call in authorize.call_args_list), 1)
            config['databases'][self.fixture.sales]['roles']['bi_viewer']['password'] = 'new-password'
            path.write_text(json.dumps(config))
            qs.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1], selected_role='bi_viewer')
            expected = 'Basic ' + base64.b64encode((trino_auth.scoped_username(self.fixture.sales, 'bi_viewer') + ':bi_viewer-old').encode()).decode()
            self.assertEqual(calls, [expected, expected])
            del self.fixture.users['alice']
            self.fixture.save()
            with self.assertRaises(qs.QueryError):
                qs.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1], selected_role='bi_viewer')
            self.assertEqual(calls[-1], expected)

    def test_native_explicit_mode_preserves_read_only_guard(self):
        import warehouse_resident as wr
        import query_service as qs
        import request_auth
        headers = self.fixture.headers()
        for name, value in (('X-Shirokuma-Role', 'data_engineer'), ('X-Trino-Catalog', 'sales'),
                            ('X-Trino-Schema', 'raw'), ('X-Shirokuma-Mode', 'read-only')):
            headers[name] = value
        ctx = self.context('raw', 'data_engineer')
        decision = request_auth.authorize_database(self.principal, ctx.database_id, ctx.selected_role,
                                                  expected_generation_id=ctx.generation_id)
        @contextmanager
        def backend():
            yield 'https://trusted:8443'
        with patch('trino_auth.prepare_backend_v2', return_value=(decision, {'X-Trino-User': 'scoped'})) as prepare, \
             patch('trino_auth.backend_ssl_context', return_value=None), patch('trino_auth.validate_backend_url'), \
             patch('warehouse_resident.persistent_trino', backend), patch.object(qs, '_transport', return_value={'id': 'q'}):
            with self.assertRaises(qs.QueryError) as error:
                wr._native_request(headers, '/v1/statement', 'POST', b'INSERT INTO events VALUES (1)',
                                   'https://public', warehouse_id='w')
            self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            prepare.assert_called_once()
            del headers['X-Shirokuma-Mode']
            headers['X-Shirokuma-Mode'] = 'sandbox-write'
            self.assertEqual(wr._native_request(headers, '/v1/statement', 'POST',
                b'INSERT INTO events VALUES (1)', 'https://public', warehouse_id='w')['id'], 'q')

    def test_raw_transport_rejects_unknown_url_method_and_origin(self):
        import query_service as qs
        pin = qs._Pin(self.context(), 'https://trusted:8443', {'Authorization': 'secret'}, None)
        url = 'https://trusted:8443/v1/statement/executing/q/0/a'
        token = qs._registered(pin, url, ('GET', 'DELETE'), 'q')
        for target, method in ((url, 'POST'), (url + '?x=1', 'GET'),
                               (url.replace('trusted', 'evil'), 'GET'), (url, 'PATCH')):
            with patch.object(qs.http.client, 'HTTPSConnection') as connection, self.assertRaises(qs.QueryError):
                qs._transport(target, method, pin)
            connection.assert_not_called()
        with patch.object(qs.http.client, 'HTTPSConnection') as connection:
            connection.return_value.getresponse.return_value.status = 200
            connection.return_value.getresponse.return_value.read.return_value = b'{"id":"q"}'
            self.assertEqual(qs._transport(url, 'GET', pin)['id'], 'q')
            self.assertEqual(connection.return_value.request.call_args.kwargs['headers']['Authorization'], 'secret')
        with self.assertRaises(qs.QueryError):
            qs._registered(pin, 'https://trusted:8443/v1/statement/executing/other/0/a', ('GET',), 'q')

    def test_active_profile_direct_entrypoints_fail_before_access(self):
        import warehouse_resident as wr
        with patch('runtime_profile.active', return_value=True), patch.object(wr, 'warehouse_activity') as activity:
            with self.assertRaises(ValueError):
                wr.query('LOCAL', 'SELECT 1')
            with self.assertRaises(ValueError):
                wr.execute('https://backend', 'SELECT 1', {})
            activity.assert_not_called()

    def test_paging_owner_revocation_restart_and_secret_snapshot(self):
        import query_service as qs
        import request_auth
        import control_store
        ctx = self.context()
        decision = request_auth.authorize_database(self.principal, ctx.database_id, ctx.selected_role,
                                                  expected_generation_id=ctx.generation_id)
        @contextmanager
        def backend():
            yield 'https://trusted:8443'
        calls = []
        def transport(url, method, pin, body=None):
            calls.append((url, method, dict(pin.headers), body))
            if method == 'POST':
                with control_store.connect() as db:
                    self.assertGreaterEqual(db.execute('SELECT count(*) FROM query_records').fetchone()[0], 1)
                return {'id': 'backend-1', 'columns': [{'name': 'value'}], 'nextUri': 'https://trusted:8443/v1/statement/executing/backend-1/0/token', 'stats': {'state': 'RUNNING'}}
            if method == 'DELETE':
                return {}
            return {'id': 'backend-1', 'data': [[1]], 'nextUri':
                'https://trusted:8443/v1/statement/executing/backend-1/1/token', 'stats': {'state': 'RUNNING'}}
        with patch('trino_auth.prepare_backend_v2', return_value=(decision, {'Authorization': 'Basic SECRET', 'X-Trino-User': 'scoped'})) as prepare, \
             patch('trino_auth.backend_ssl_context', return_value=None), \
             patch('trino_auth.validate_backend_url'), patch('warehouse_resident.persistent_trino', backend), \
             patch.object(qs, '_transport', side_effect=transport):
            sql = 'SELECT * FROM events'
            page = qs.submit(self.principal, ctx, sql, mode='read-only', max_rows=100, request_id='r', worksheet_id='w')
            token = page['next_uri'].rsplit('/', 1)[1]
            self.assertEqual(calls[0][3], sql.encode())
            self.assertEqual(prepare.call_count, 1)
            with patch.object(qs, '_transport', side_effect=qs.QueryError('QUERY_BACKEND_FAILED', 502)):
                with self.assertRaises(qs.QueryError):
                    qs.cancel(self.principal, page['id'], selected_role='bi_viewer')
                self.assertEqual(qs._row(page['id'])['state'], 'UNKNOWN')
            with self.assertRaises(qs.QueryError) as unknown:
                qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            self.assertEqual(unknown.exception.code, 'ADMISSION_CONFLICT')
            qs.cancel(self.principal, page['id'], selected_role='bi_viewer')
            page = qs.submit(self.principal, ctx, sql, mode='read-only', max_rows=100, request_id='r', worksheet_id='w')
            token = page['next_uri'].rsplit('/', 1)[1]
            self.assertNotIn('SECRET', repr(qs._pins))
            with control_store.connect() as db:
                self.assertNotIn('SECRET', repr([tuple(r) for r in db.execute('SELECT * FROM query_execution_contexts')]))
            with self.assertRaises(qs.QueryError) as error:
                qs.fetch(self.fixture.principal('owner'), page['id'], token, selected_role='admin')
            self.assertEqual(error.exception.status, 404)
            for bad in ('bogus', token + '?x=1'):
                with self.assertRaises(qs.QueryError):
                    qs.fetch(self.principal, page['id'], bad, selected_role='bi_viewer')
            with self.assertRaises(qs.QueryError):
                qs.fetch(self.principal, page['id'], token, selected_role='data_engineer')
            fetched = qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            self.assertEqual(fetched['data'], [[1]])
            self.assertEqual(fetched['columns'], [{'name': 'value'}])
            self.assertEqual(calls[-1][2]['Authorization'], 'Basic SECRET')
            import warehouse_resident as wr
            headers = self.fixture.headers()
            headers['X-Shirokuma-Role'] = 'bi_viewer'
            headers['X-Trino-Catalog'] = 'finance'
            headers['X-Trino-Schema'] = 'marts'
            with self.assertRaises(qs.QueryError) as error:
                wr._native_request(headers, '/v1/statement/' + page['id'] + '/pages/' + token,
                                   'GET', b'', 'https://public')
            self.assertEqual(error.exception.code, 'CONTEXT_MISMATCH')
            del headers['X-Trino-Catalog']
            headers['X-Trino-Catalog'] = 'sales'
            headers['X-Shirokuma-Mode'] = 'sandbox-write'
            with self.assertRaises(qs.QueryError) as error:
                wr._native_request(headers, '/v1/statement/' + page['id'] + '/pages/' + token,
                                   'GET', b'', 'https://public')
            self.assertEqual(error.exception.code, 'CONTEXT_MISMATCH')
            del headers['X-Shirokuma-Mode']
            with control_store.connect() as db:
                db.execute("UPDATE grants SET maximum_role='revoked',revision=revision+1 WHERE principal_id='alice'")
            with self.assertRaises(qs.QueryError) as error:
                qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
            self.assertEqual(calls[-1][1], 'DELETE')
            self.assertEqual(wr._native_request(headers, '/v1/statement/' + page['id'] + '/pages/' + token,
                'DELETE', b'', 'https://public')['state'], 'CANCELED')
            self.assertEqual(calls[-1][0], 'https://trusted:8443/v1/statement/executing/backend-1/1/token')
            with control_store.connect() as db:
                db.execute("UPDATE grants SET maximum_role='bi_viewer',revision=revision+1 WHERE principal_id='alice'")
            qs._pins.clear()
            count = len(calls)
            with self.assertRaises(qs.QueryError) as error:
                qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
            self.assertEqual(len(calls), count)


if __name__ == '__main__':
    unittest.main()
