"""Saved quality-test behavior through existing admitted DataOps entrypoints."""
from contextlib import contextmanager
import unittest
from unittest.mock import patch

import test_db_dbt as harness

dbt, ds, wap = harness.dbt, harness.ds, harness.wap
control_store, qs, QueryError = harness.control_store, harness.qs, harness.QueryError


class SavedQualityBoundaryTests(unittest.TestCase):
    @contextmanager
    def project(self, *, view=False):
        fixture = harness.DbtTests()
        try:
            fixture.setUp()
            if view:
                fixture.view_project()
            yield fixture
        finally:
            fixture.doCleanups()

    def at_first_test(self, fixture, check):
        """Exercise the real saved child while its admission/owner scope is live."""
        original = ds.Execution._dbt_test
        visited = []
        failures = []

        def test(execution):
            if visited:
                return original(execution)
            visited.append(execution.id)
            try:
                return check(execution, original)
            except BaseException as error:
                failures.append(error)
                raise

        with patch.object(ds.Execution, '_dbt_test', new=test):
            run = fixture.build_run()
        if failures:
            raise failures[0]
        self.assertEqual(len(visited), 1)
        self.assertEqual(run['state'], 'READY', run)
        self.assertEqual([t['failure_count'] for t in run['tests']], [0, 0])
        return run

    def test_saved_phase_and_child_link_reject_before_preparation(self):
        with self.project() as fixture:
            def check(execution, original):
                for invalid in ('phase', 'saved_phase', 'child_link'):
                    with self.subTest(invalid=invalid):
                        scope = ds._dbt.get()
                        token = None
                        if invalid == 'phase':
                            token = ds._dbt.set(scope[:6] + ('build',) + scope[7:])
                        elif invalid == 'saved_phase':
                            fixture.mutate(scope[5], lambda run: run.update(_phase='build'))
                        else:
                            with control_store.connect() as db:
                                db.execute("UPDATE dbt_run_operations SET node_id='test.other' WHERE operation_id=?",
                                           (execution.id,))
                        try:
                            with (patch.object(dbt, '_test_select', wraps=dbt._test_select) as select,
                                  patch.object(dbt, '_pinned', wraps=dbt._pinned) as pinned,
                                  patch.object(wap, '_compile_model', wraps=wap._compile_model) as compile,
                                  patch.object(execution, '_execute', wraps=execution._execute) as send):
                                with self.assertRaises(QueryError) as caught:
                                    original(execution)
                                self.assertEqual(caught.exception.code,
                                                 'DBT_CONTINUATION_REQUIRED' if invalid == 'phase' else 'DBT_RUN_STOPPED')
                                if invalid == 'phase':
                                    select.assert_called_once_with(execution)
                                else:
                                    select.assert_not_called()  # Existing operation guard rejects first.
                                pinned.assert_not_called()
                                compile.assert_not_called()
                                send.assert_not_called()
                        finally:
                            if token is not None:
                                ds._dbt.reset(token)
                            elif invalid == 'saved_phase':
                                fixture.mutate(scope[5], lambda run: run.update(_phase=scope[6]))
                            else:
                                with control_store.connect() as db:
                                    db.execute('UPDATE dbt_run_operations SET node_id=? WHERE operation_id=?',
                                               (scope[7], execution.id))
                return original(execution)
            self.at_first_test(fixture, check)

    def test_table_and_view_sql_pins_count_and_no_transaction_at_send(self):
        for view in (False, True):
            with self.subTest(view=view), self.project(view=view) as fixture:
                def check(execution, original):
                    compiled, sent, events = [], [], []
                    compile_model = wap._compile_model

                    def compile(*args, **kwargs):
                        sql = compile_model(*args, **kwargs)
                        if kwargs.get('execution') is execution:
                            events.append('compile')
                            compiled.append((sql, args[1], kwargs['measurements']))
                        return sql

                    def transport(url, method, pin, body=None):
                        if body and body.decode().startswith('SELECT COUNT(*)'):
                            events.append('send')
                            # A separate connection must be able to acquire the writer lock.
                            with control_store.connect() as db:
                                db.execute('PRAGMA busy_timeout=0')
                                db.execute('BEGIN IMMEDIATE')
                                db.rollback()
                            sent.append(body.decode())
                            self.assertEqual(binding.call_count, 2 if view else 0)
                        return fixture.w.transport(url, method, pin, body)

                    with (patch.object(dbt, '_test_select', wraps=dbt._test_select) as select,
                          patch.object(dbt, '_pinned', wraps=dbt._pinned) as pinned,
                          patch.object(dbt, '_view_binding', wraps=dbt._view_binding) as binding,
                          patch.object(wap, '_compile_model', side_effect=compile),
                          patch.object(qs, '_transport', side_effect=transport)):
                        self.assertEqual(original(execution), 0)
                        select.assert_called_once_with(execution)
                        pinned.assert_called_once()
                        self.assertEqual(len(compiled), 1)
                        self.assertEqual(binding.call_count, 3 if view else 0)
                    self.assertEqual(events, ['compile', 'send'])
                    sql, sources, inputs = compiled[0]
                    self.assertEqual(sent, ['SELECT COUNT(*) FROM (' + sql + ') AS dbt_failures'])
                    self.assertEqual(pinned.call_args.args, (execution, sources))
                    self.assertEqual(set(sources), set(inputs))
                    self.assertEqual(len(sources), 1)
                    for alias, ref in sources.items():
                        self.assertEqual(inputs[alias]['ref'], harness.asdict(ref))
                        if view:
                            self.assertEqual(inputs[alias]['kind'], 'VIEW')
                            self.assertNotIn('FOR VERSION AS OF', sql)
                            self.assertTrue(all(call == binding.call_args_list[0]
                                                for call in binding.call_args_list))
                            self.assertEqual(binding.call_args.kwargs, {'expected': inputs[alias]})
                        else:
                            self.assertIn('FOR VERSION AS OF ' + inputs[alias]['snapshot'], sql)
                    return 0
                self.at_first_test(fixture, check)

    def test_compilation_failure_does_not_send_count(self):
        with self.project(view=True) as fixture:
            def check(execution, original):
                error = ValueError('test compilation failed')
                compile_model = wap._compile_model
                attempted = []

                def compile(*args, **kwargs):
                    if kwargs.get('execution') is execution:
                        attempted.append(kwargs)
                        raise error
                    return compile_model(*args, **kwargs)

                with (patch.object(dbt, '_test_select', wraps=dbt._test_select) as select,
                      patch.object(dbt, '_pinned', wraps=dbt._pinned) as pinned,
                      patch.object(dbt, '_view_binding', wraps=dbt._view_binding) as binding,
                      patch.object(wap, '_compile_model', side_effect=compile),
                      patch.object(execution, '_execute', wraps=execution._execute) as send):
                    with self.assertRaises(ValueError) as caught:
                        original(execution)
                    self.assertIs(caught.exception, error)
                    select.assert_called_once_with(execution)
                    pinned.assert_called_once()
                    self.assertEqual(len(attempted), 1)
                    self.assertEqual(binding.call_count, 1)  # Pin check only.
                    send.assert_not_called()
                return original(execution)
            self.at_first_test(fixture, check)

    def test_query_exception_baseexception_and_missing_data_skip_view_postcheck(self):
        with self.project(view=True) as fixture:
            def check(execution, original):
                for error in (RuntimeError('query failed'), BaseException('interrupted'), None):
                    with self.subTest(error=type(error).__name__):
                        view = fixture.view_metadata()
                        version = view['current-version-id']

                        def send(*args):
                            self.assertEqual(args[0::2], ('dbt_test', 'test'))
                            self.assertEqual(binding.call_count, 2)
                            # A finally-based post-check would mask the original failure.
                            view['current-version-id'] = version + 1
                            if error is not None:
                                raise error
                            return {}  # ['data'] fails within the query lifetime.

                        try:
                            with (patch.object(dbt, '_view_binding', wraps=dbt._view_binding) as binding,
                                  patch.object(execution, '_execute', side_effect=send) as transport):
                                with self.assertRaises(type(error) if error is not None else KeyError) as caught:
                                    original(execution)
                                if error is not None:
                                    self.assertIs(caught.exception, error)
                                else:
                                    self.assertEqual(caught.exception.args, ('data',))
                                self.assertEqual(binding.call_count, 2)
                                transport.assert_called_once()
                        finally:
                            view['current-version-id'] = version
                return original(execution)
            self.at_first_test(fixture, check)

    def test_view_change_precedes_malformed_count_validation(self):
        with self.project(view=True) as fixture:
            def check(execution, original):
                view = fixture.view_metadata()
                version = view['current-version-id']

                def send(*args):
                    self.assertEqual(binding.call_count, 2)
                    view['current-version-id'] = version + 1
                    return {'data': []}

                try:
                    with (patch.object(dbt, '_view_binding', wraps=dbt._view_binding) as binding,
                          patch.object(execution, '_execute', side_effect=send) as transport):
                        with self.assertRaises(QueryError) as caught:
                            original(execution)
                        self.assertEqual(caught.exception.code, 'DBT_VIEW_CHANGED')
                        self.assertEqual(binding.call_count, 3)
                        transport.assert_called_once()
                finally:
                    view['current-version-id'] = version
                return original(execution)
            self.at_first_test(fixture, check)

    def test_stable_view_checks_before_strict_count_validation(self):
        with self.project(view=True) as fixture:
            def check(execution, original):
                cases = ([], [[]], [[0, 1]], [[0], [0]], [[True]], [[-1]], [['0']], [[0]], [[7]])
                for rows in cases:
                    with self.subTest(rows=rows):
                        def send(*args):
                            self.assertEqual(binding.call_count, 2)
                            return {'data': rows}

                        with (patch.object(dbt, '_view_binding', wraps=dbt._view_binding) as binding,
                              patch.object(execution, '_execute', side_effect=send) as transport):
                            if rows in ([[0]], [[7]]) and type(rows[0][0]) is int:
                                self.assertEqual(original(execution), rows[0][0])
                            else:
                                with self.assertRaises(QueryError) as caught:
                                    original(execution)
                                self.assertEqual(caught.exception.code, 'INVALID_DBT_TEST_RESULT')
                            self.assertEqual(binding.call_count, 3)
                            self.assertTrue(all(call == binding.call_args_list[0]
                                                for call in binding.call_args_list))
                            transport.assert_called_once()
                return original(execution)
            self.at_first_test(fixture, check)


if __name__ == '__main__':
    unittest.main()
