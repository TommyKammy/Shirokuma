"""Nested fixture ownership contracts; all backends remain local test doubles."""
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fixture_lifecycle import setup_fixture
import test_db_dbt as dbt_tests
import test_manual_wap as wap_tests
import test_dataops_service as dataops_tests
import test_request_auth as auth_tests
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
