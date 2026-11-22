"""DataOps-owned dbt continuation lifetime and authority contracts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
import unittest
from unittest.mock import patch

import test_db_dbt as harness

ds, dbt, qs = harness.ds, harness.dbt, harness.qs
admission, control_store = harness.admission, harness.control_store


class DbtContinuationTests(unittest.TestCase):
    def test_binding_identity_immutability_and_nested_reset(self):
        self.assertIsNone(ds.current_dbt_continuation())
        pins = tuple(object() for _ in range(4))
        args = (*pins, 'credential', 'run', 'build', 'model.first')
        with ds.dbt_child_scope(*args):
            outer = ds.current_dbt_continuation()
            self.assertIsInstance(outer, ds.DbtContinuation)
            self.assertEqual(outer, args)  # Generic DataOps tuple compatibility.
            for actual, original in zip(outer.owner_pin, args):
                self.assertIs(actual, original)
            self.assertEqual(outer.child_key, ('run', 'build', 'model.first'))
            for field, value in (('phase', 'test'), ('owner_pin', ()), ('child_key', ())):
                with self.assertRaises(AttributeError):
                    setattr(outer, field, value)
            for error in (None, Exception('failed'), BaseException('interrupted')):
                with self.subTest(error=type(error).__name__):
                    try:
                        with ds.dbt_child_scope(*pins, 'credential', 'nested', 'test', 'test.first'):
                            inner = ds.current_dbt_continuation()
                            self.assertIsNot(inner, outer)
                            self.assertEqual(inner.child_key, ('nested', 'test', 'test.first'))
                            if error is not None:
                                raise error
                    except BaseException as caught:
                        self.assertIs(caught, error)
                    self.assertIs(ds.current_dbt_continuation(), outer)
        self.assertIsNone(ds.current_dbt_continuation())
        for error in (Exception('outer failed'), BaseException('outer interrupted')):
            with self.assertRaises(type(error)) as caught:
                with ds.dbt_child_scope(*args):
                    raise error
            self.assertIs(caught.exception, error)
            self.assertIsNone(ds.current_dbt_continuation())

    def test_threads_have_independent_bindings(self):
        barrier = Barrier(2)
        args = (object(), object(), object(), object(), 'credential')

        def worker(run_id):
            self.assertIsNone(ds.current_dbt_continuation())
            with ds.dbt_child_scope(*args, run_id, 'build', 'model.first'):
                binding = ds.current_dbt_continuation()
                barrier.wait(timeout=5)
                self.assertIs(ds.current_dbt_continuation(), binding)
                self.assertEqual(binding.run_id, run_id)
            self.assertIsNone(ds.current_dbt_continuation())

        with ds.dbt_child_scope(*args, 'main', 'test', 'test.first'):
            main = ds.current_dbt_continuation()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker, run_id) for run_id in ('one', 'two')]
                for future in futures:
                    future.result(timeout=10)
            self.assertIs(ds.current_dbt_continuation(), main)
        self.assertIsNone(ds.current_dbt_continuation())


class DbtAdmissionScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = harness.DbtTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_real_child_inherits_pin_and_link_and_rejects_mismatches_first(self):
        fixture = self.fixture
        begin = ds.begin
        visited, failures = [], []

        def check(principal, context, **kwargs):
            scope = ds.current_dbt_continuation()
            if scope is None or visited:
                return begin(principal, context, **kwargs)
            visited.append(scope.child_key)
            try:
                self.assertEqual(scope.owner_pin, dbt._live[scope.run_id][:5])
                # Valid alternative actors/contexts reach the inherited-scope comparison.
                for actor, target in ((fixture.admin, fixture.admin_context),
                                      (principal, fixture.b.context(schema='raw'))):
                    with (patch.object(admission, 'admit', wraps=admission.admit) as admit,
                          patch.object(dbt, '_check_child', wraps=dbt._check_child) as guard,
                          patch.object(qs, '_capture_dataops', side_effect=AssertionError('recapture')),
                          patch.object(qs, '_transport', wraps=qs._transport) as send):
                        with self.assertRaises(harness.QueryError) as caught:
                            begin(actor, target, **dict(kwargs, idempotency_key='mismatch-' + target.schema_id))
                        self.assertEqual(caught.exception.code, 'CONTEXT_MISMATCH')
                        guard.assert_not_called()
                        admit.assert_not_called()
                        send.assert_not_called()
                # Invalid lower-family continuations must not override this genuine dbt child.
                with ExitStack() as stack:
                    stack.enter_context(ds._job_scope(*scope.owner_pin, 'missing-job', 1))
                    stack.enter_context(ds._stage_scan_scope(*scope.owner_pin, 'missing-scan', 'item', 1))
                    stack.enter_context(ds._mart_scope(*scope.owner_pin, 'missing-mart', 'build'))
                    stack.enter_context(patch.object(qs, '_capture_dataops', side_effect=AssertionError('recapture')))
                    stack.enter_context(patch.object(ds.secrets, 'token_urlsafe', side_effect=AssertionError('new credential')))
                    admit = stack.enter_context(patch.object(admission, 'admit', wraps=admission.admit))
                    execution = begin(principal, context, **kwargs)
                self.assertIs(admit.call_args.kwargs['parent'], scope.parent)
                self.assertIs(ds._live[execution.id][0], scope.frozen)
                self.assertIs(ds.current_dbt_continuation(), scope)
                with control_store.connect() as db:
                    link = db.execute('SELECT run_id,phase,node_id FROM dbt_run_operations WHERE operation_id=?',
                                      (execution.id,)).fetchone()
                    self.assertEqual(tuple(link), scope.child_key)
                    op = db.execute('SELECT credential_snapshot_ref FROM dataops_operations WHERE id=?',
                                    (execution.id,)).fetchone()
                    self.assertEqual(op[0], scope.credential_ref)
                return execution
            except BaseException as error:
                failures.append(error)
                raise

        with patch.object(ds, 'begin', side_effect=check):
            run = fixture.build_run()
        if failures:
            raise failures[0]
        self.assertEqual(len(visited), 1)
        self.assertEqual(run['state'], 'READY', run)
        self.assertIsNone(ds.current_dbt_continuation())

    def test_lower_family_precedence_preserves_real_guards(self):
        import db_marts
        fixture = self.fixture
        # Obtain a genuine frozen transport pin and active parent from the SQLite fixture.
        root = ds.begin(fixture.engineer, fixture.context, route='scope-root',
                        idempotency_key='root', payload={})
        self.addCleanup(root.cancel)
        frozen, parent, _, _ = ds._live[root.id]
        pin = (fixture.engineer, fixture.context, parent, frozen, 'existing-credential')
        for highest in ('mart', 'scan', 'job'):
            with self.subTest(highest=highest), ExitStack() as stack:
                stack.enter_context(ds._job_scope(*pin, 'missing-job', 1))
                if highest in ('mart', 'scan'):
                    stack.enter_context(ds._stage_scan_scope(*pin, 'missing-scan', 'item', 1))
                if highest == 'mart':
                    stack.enter_context(ds._mart_scope(*pin, 'missing-mart', 'build'))
                mart = stack.enter_context(patch.object(db_marts, '_check_child', wraps=db_marts._check_child))
                scan = stack.enter_context(patch.object(ds, '_check_stage_scan', wraps=ds._check_stage_scan))
                job = stack.enter_context(patch.object(ds, '_check_job', wraps=ds._check_job))
                admit = stack.enter_context(patch.object(admission, 'admit', wraps=admission.admit))
                stack.enter_context(patch.object(qs, '_capture_dataops', side_effect=AssertionError('recapture')))
                with self.assertRaises(harness.QueryError):
                    ds.begin(fixture.engineer, fixture.context, route='scope-child',
                             idempotency_key=highest, payload={})
                for family, guard in (('mart', mart), ('scan', scan), ('job', job)):
                    self.assertEqual(guard.call_count, int(family == highest))
                admit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
