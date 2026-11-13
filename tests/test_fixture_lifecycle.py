"""Nested fixture ownership contracts; all backends remain local test doubles."""
import os
import sys
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fixture_lifecycle import setup_fixture
import test_db_dbt as dbt_tests
import test_manual_wap as wap_tests
import test_dataops_service as dataops_tests
import test_request_auth as auth_tests
import test_query_service as query_tests
import trino_auth
import warehouse_resident


class FixtureLifecycleTests(unittest.TestCase):
    def test_success_transfers_delayed_lifo_cleanup_exactly_once(self):
        events = []

        class Child(unittest.TestCase):
            def setUp(self):
                self.addCleanup(events.append, 'child-first')
                self.addCleanup(events.append, 'child-last')

        parent, child = unittest.TestCase(), Child()
        parent.addCleanup(events.append, 'parent-first')
        with patch.object(child, 'doCleanups', wraps=child.doCleanups) as cleanup:
            self.assertIs(setup_fixture(parent, child), child)
            parent.addCleanup(events.append, 'parent-last')
            self.assertEqual(events, [])
            cleanup.assert_not_called()
            self.assertTrue(parent.doCleanups())
            self.assertTrue(parent.doCleanups())
            cleanup.assert_called_once_with()
        self.assertEqual(events, ['parent-last', 'child-last', 'child-first', 'parent-first'])

    def test_partial_setup_error_and_skip_unwind_immediately(self):
        for error in (ValueError('setup failed'), unittest.SkipTest('skip setup')):
            with self.subTest(error=type(error).__name__):
                events = []

                class Child(unittest.TestCase):
                    def setUp(self):
                        self.addCleanup(events.append, 'first')
                        self.addCleanup(events.append, 'last')
                        raise error

                parent, child = unittest.TestCase(), Child()
                with patch.object(child, 'doCleanups', wraps=child.doCleanups) as cleanup:
                    with self.assertRaises(type(error)) as caught:
                        setup_fixture(parent, child)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(events, ['last', 'first'])
                    self.assertTrue(parent.doCleanups())
                    cleanup.assert_called_once_with()

    def test_cleanup_failure_is_reported_and_other_cleanups_still_run(self):
        events = []

        def fail():
            events.append('failing')
            raise ValueError('cleanup broke')

        class Child(unittest.TestCase):
            def setUp(self):
                self.addCleanup(events.append, 'child-first')
                self.addCleanup(fail)
                self.addCleanup(events.append, 'child-last')

        class Parent(unittest.TestCase):
            def runTest(self):
                self.addCleanup(events.append, 'parent-first')
                setup_fixture(self, Child())
                self.addCleanup(events.append, 'parent-last')

        result = unittest.TestResult()
        Parent().run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.errors), 1)
        self.assertIn('Child fixture cleanup failed', result.errors[0][1])
        self.assertEqual(events, ['parent-last', 'child-last', 'failing',
                                  'child-first', 'parent-first'])

    def test_setup_and_cleanup_failure_reports_cleanup_with_setup_context(self):
        events = []
        setup_error = ValueError('setup broke')

        def fail():
            raise OSError('cleanup broke')

        class Child(unittest.TestCase):
            def setUp(self):
                self.addCleanup(events.append, 'unwound')
                self.addCleanup(fail)
                raise setup_error

        parent = unittest.TestCase()
        with self.assertRaisesRegex(RuntimeError, 'Child fixture cleanup failed') as caught:
            setup_fixture(parent, Child())
        self.assertIs(caught.exception.__context__, setup_error)
        self.assertEqual(events, ['unwound'])
        self.assertTrue(parent.doCleanups())


class QueryFixtureFailureTests(unittest.TestCase):
    def test_real_query_callers_unwind_failed_child_setup(self):
        callers = (
            (query_tests.QueryStatementWireTests, 'setUp', 'dataops-backend'),
            (query_tests.QueryPollCleanupTests, 'setUp', 'dataops-backend'),
            (query_tests.QueryOriginLivenessTests, 'setUp', 'dataops-backend'),
            (query_tests.QueryHistoryTests,
             'test_retirement_after_root_admission_commits_runs_once_but_denies_response',
             'dataops-backend'),
            (query_tests.QueryHistoryTests,
             'test_retiring_root_query_read_page_history_denied_original_pin_cancel_allowed',
             'dataops-backend'),
            (query_tests.QueryHistoryTests,
             'test_history_omits_stale_dataops_records_without_rejecting_current_database',
             'dataops-backend'),
            (query_tests.QueryExecutionTests, 'setUp', 'principal-environment'),
        )
        for caller, method, stage in callers:
            with self.subTest(caller=caller.__name__, method=method, stage=stage):
                self.check_query_failure(caller, method, stage)

    def check_query_failure(self, caller, method, stage):
        principal_setup = auth_tests.PrincipalTests.setUp
        dataops_setup = dataops_tests.DataOpsTests.setUp
        start_patch = dataops_tests.DataOpsTests.start_patch
        clear_pins = dataops_tests.DataOpsTests.clear_pins
        connect = auth_tests.control_store.connect
        observed, events, cleanups = {}, [], {}
        failure = RuntimeError('injected ' + stage)
        originals = [(trino_auth, name, getattr(trino_auth, name)) for name in
                     ('prepare_backend_v2', 'backend_ssl_context', 'validate_backend_url')]
        originals += [(warehouse_resident, name, getattr(warehouse_resident, name)) for name in
                      ('persistent_trino', 'warehouse_activity')]
        originals += [(dataops_tests.qs, '_transport', dataops_tests.qs._transport),
                      (dataops_tests.ds.inventory, 'catalog_api', dataops_tests.ds.inventory.catalog_api)]
        sentinel = SimpleNamespace(preexisting=True)
        parent = caller()

        with ExitStack() as stack:
            def observe(fixture, kind):
                observed[kind] = fixture
                cleanups[kind] = stack.enter_context(
                    patch.object(fixture, 'doCleanups', wraps=fixture.doCleanups))
                fixture.addCleanup(events.append, kind + '-first')

            def install_principal(fixture):
                observe(fixture, 'principal')
                if stage == 'principal-environment':
                    def fail_connect():
                        self.assertEqual(os.environ['SHIROKUMA_AUTH_FILE'], str(fixture.accounts))
                        self.assertEqual(os.environ['SHIROKUMA_STATE_DIR'], str(fixture.root / 'state'))
                        self.assertTrue(fixture.accounts.is_file())
                        fixture.addCleanup(events.append, 'principal-last')
                        raise failure
                    with patch.object(auth_tests.control_store, 'connect', side_effect=fail_connect):
                        principal_setup(fixture)
                else:
                    principal_setup(fixture)
                    fixture.addCleanup(events.append, 'principal-last')

            def install_dataops(fixture):
                observe(fixture, 'dataops')
                dataops_setup(fixture)

            def install_backend(fixture, target, *args, **kwargs):
                result = start_patch(fixture, target, *args, **kwargs)
                if target == 'warehouse_resident.persistent_trino':
                    self.assertIsNot(warehouse_resident.persistent_trino, originals[3][2])
                    fixture.addCleanup(events.append, 'dataops-last')
                    raise failure
                return result

            def check_clear_pins(fixture):
                if hasattr(fixture, 'objects'):
                    # DataOps cleanup must run while its Principal storage and
                    # environment are still usable.
                    self.assertTrue(observed['principal'].root.is_dir())
                    with connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], 3)
                    events.append('pins-before-storage')
                clear_pins(fixture)

            stack.enter_context(patch.dict(os.environ, {
                'SHIROKUMA_AUTH_MODE': 'preexisting-mode',
                'SHIROKUMA_AUTH_FILE': 'preexisting-auth',
                'SHIROKUMA_STATE_DIR': 'preexisting-state',
                'FIXTURE_LIFETIME_SENTINEL': 'keep-me'}))
            stack.enter_context(patch.dict(sys.modules, warehouse_lifecycle=sentinel))
            stack.enter_context(patch.object(auth_tests.PrincipalTests, 'setUp', install_principal))
            stack.enter_context(patch.object(dataops_tests.DataOpsTests, 'setUp', install_dataops))
            stack.enter_context(patch.object(dataops_tests.DataOpsTests, 'start_patch', install_backend))
            stack.enter_context(patch.object(dataops_tests.DataOpsTests, 'clear_pins', check_clear_pins))
            environment = dict(os.environ)
            # An existing parent-owned function patch must survive child failure.
            parent.addCleanup(events.append, 'parent-first')
            parent_patch = patch.object(dataops_tests.qs, '_transport')
            parent_transport = parent_patch.start()
            parent.addCleanup(parent_patch.stop)
            try:
                with self.assertRaises(RuntimeError) as caught:
                    getattr(parent, method)()
                self.assertIs(caught.exception, failure)
                self.assertFalse(observed['principal'].root.exists())
                self.assertEqual(dict(os.environ), environment)
                for module, name, original in originals:
                    expected = parent_transport if name == '_transport' else original
                    self.assertIs(getattr(module, name), expected, name)
                if stage == 'principal-environment':
                    # QueryExecution installs this module patch before its child.
                    self.assertIsNot(sys.modules['warehouse_lifecycle'], sentinel)
                    expected_events = ['principal-last', 'principal-first']
                else:
                    self.assertIs(sys.modules['warehouse_lifecycle'], sentinel)
                    expected_events = ['dataops-last', 'pins-before-storage',
                                       'principal-last', 'principal-first', 'dataops-first']
                self.assertEqual(events, expected_events)
                for cleanup in cleanups.values():
                    cleanup.assert_called_once_with()
                parent.addCleanup(events.append, 'parent-last')
                self.assertTrue(parent.doCleanups())
                self.assertTrue(parent.doCleanups())
                self.assertEqual(events, expected_events + ['parent-last', 'parent-first'])
                self.assertIs(sys.modules['warehouse_lifecycle'], sentinel)
                self.assertIs(dataops_tests.qs._transport, originals[-2][2])
                for cleanup in cleanups.values():
                    cleanup.assert_called_once_with()
            finally:
                self.assertTrue(parent.doCleanups())
                # Also release an unowned child if a regression prevents the
                # immediate unwind, so a failing subtest cannot contaminate others.
                for kind in ('dataops', 'principal'):
                    if kind in observed and observed[kind]._cleanups:
                        self.assertTrue(observed[kind].doCleanups())


class DbtFixtureFailureTests(unittest.TestCase):
    def test_real_chain_unwinds_at_each_installation_boundary(self):
        roots = []
        for stage in ('principal-environment', 'dataops-backend', 'wap-catalog'):
            with self.subTest(stage=stage):
                roots.append(self.check_chain_failure(stage))
        self.assertEqual(len(set(roots)), 3)

    def check_chain_failure(self, stage):
        principal_setup = auth_tests.PrincipalTests.setUp
        dataops_patch = dataops_tests.DataOpsTests.start_patch
        clear_pins = dataops_tests.DataOpsTests.clear_pins
        wap_setup = wap_tests.ManualWapTests.setUp
        connect = auth_tests.control_store.connect
        observed = {}
        cleanup_checks = []
        failure = RuntimeError('injected ' + stage)

        def record_principal(fixture):
            observed['principal'] = fixture
            if stage == 'principal-environment':
                # The first state connection follows installation of the auth
                # environment. Fail there, inside the real Principal setUp.
                def fail_connect():
                    self.assertEqual(os.environ['SHIROKUMA_AUTH_FILE'], str(fixture.accounts))
                    self.assertTrue(fixture.accounts.is_file())
                    raise failure
                with patch.object(auth_tests.control_store, 'connect', side_effect=fail_connect):
                    principal_setup(fixture)
            else:
                principal_setup(fixture)
                with connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], 2)
                self.assertEqual(set(fixture.users), {'alice', 'owner'})

        def install_backend(fixture, target, *args, **kwargs):
            result = dataops_patch(fixture, target, *args, **kwargs)
            if stage == 'dataops-backend' and target == 'warehouse_resident.persistent_trino':
                self.assertIsNot(warehouse_resident.persistent_trino, originals[3][2])
                raise failure
            return result

        def check_clear_pins(fixture):
            # This also runs during setup. Only record the later cleanup call.
            if 'principal' in observed and hasattr(fixture, 'objects'):
                root = observed['principal'].root
                self.assertTrue(root.is_dir())
                self.assertEqual(os.environ['SHIROKUMA_STATE_DIR'], str(root / 'state'))
                with connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], 3)
                cleanup_checks.append('pins-before-storage')
            clear_pins(fixture)

        def install_wap(fixture):
            wap_setup(fixture)
            self.assertIsNot(wap_tests.qs._transport, originals[-2][2])
            self.assertIsNot(wap_tests.ds.inventory.catalog_api, originals[-1][2])
            if stage == 'wap-catalog':
                raise failure

        originals = [(trino_auth, name, getattr(trino_auth, name)) for name in
                     ('prepare_backend_v2', 'backend_ssl_context', 'validate_backend_url')]
        originals += [(warehouse_resident, name, getattr(warehouse_resident, name)) for name in
                      ('persistent_trino', 'warehouse_activity')]
        originals += [(wap_tests.qs, '_transport', wap_tests.qs._transport),
                      (wap_tests.ds.inventory, 'catalog_api', wap_tests.ds.inventory.catalog_api)]
        sentinel = SimpleNamespace(preexisting=True)
        prior_environment = {'SHIROKUMA_AUTH_MODE': 'preexisting-mode',
                             'SHIROKUMA_AUTH_FILE': 'preexisting-auth',
                             'SHIROKUMA_STATE_DIR': 'preexisting-state',
                             'FIXTURE_LIFETIME_SENTINEL': 'keep-me'}
        parent = dbt_tests.DbtTests()
        with patch.dict(os.environ, prior_environment), \
                patch.dict(sys.modules, warehouse_lifecycle=sentinel), \
                patch.object(auth_tests.PrincipalTests, 'setUp', record_principal), \
                patch.object(dataops_tests.DataOpsTests, 'start_patch', install_backend), \
                patch.object(dataops_tests.DataOpsTests, 'clear_pins', check_clear_pins), \
                patch.object(wap_tests.ManualWapTests, 'setUp', install_wap):
            environment = dict(os.environ)
            try:
                with self.assertRaises(RuntimeError) as caught:
                    parent.setUp()
                self.assertIs(caught.exception, failure)
                # Inspect before parent cleanup: a failing nested setUp must
                # release its resources immediately, without caller assistance.
                fixture = observed['principal']
                self.assertFalse(fixture.root.exists())
                self.assertEqual(dict(os.environ), environment)
                self.assertIs(sys.modules['warehouse_lifecycle'], sentinel)
                for module, name, original in originals:
                    self.assertIs(getattr(module, name), original, name)
                expected = [] if stage == 'principal-environment' else ['pins-before-storage']
                self.assertEqual(cleanup_checks, expected)
            finally:
                self.assertTrue(parent.doCleanups())
        return observed['principal'].root
