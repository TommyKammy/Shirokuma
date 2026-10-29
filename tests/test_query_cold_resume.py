"""Cold ingress exercises durable intents against event-controlled boundaries."""
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from contextlib import ExitStack, contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import dataops_service as ds
import query_service as qs
import warehouse_admission as admission
import warehouse_resident as wr
import warehouse_lifecycle as lifecycle_core
import test_dataops_service as fixtures


class ColdQueryTests(unittest.TestCase):
    def setUp(self):
        self.persistent_trino = wr.persistent_trino
        self.f = fixtures.DataOpsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.entered, self.release = threading.Event(), threading.Event()
        self.workers = []
        self.lifecycle = sys.modules['warehouse_lifecycle']
        self.lifecycle.applicable = lambda: True
        self.lifecycle.requires_wake = lambda: True
        self.lifecycle.ensure_awake = self.wake
        self.transport = Mock(return_value={'id': 'backend', 'data': [[42]],
            'nextUri': 'http://127.0.0.1:1234/v1/statement/backend/1'})
        self.f.start_patch('query_service._transport', self.transport)
        self.addCleanup(self.finish_workers)

    def finish_workers(self):
        self.release.set()
        for pin in self.workers:
            self.assertTrue(pin.initial_done.wait(3), 'initial worker did not settle')

    def wake(self, *, actor, authorize, cancelled):
        self.assertEqual(actor, self.f.principal.principal_id)
        authorize()
        # A write can be acquired during Wake; intent already exists.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM query_records').fetchone()[0], 1)
        self.entered.set()
        self.assertTrue(self.release.wait(3))
        # Core may continue its shared Wake even after this waiter cancels.
        authorize()

    def submit(self):
        page = qs.submit(self.f.principal, self.f.context(), 'SELECT 42', mode='read-only',
            max_rows=100, request_id='correlation', worksheet_id='worksheet')
        self.workers.append(qs._pins[page['id']])
        return page

    def fetch(self, page):
        return qs.fetch(self.f.principal, page['id'], page['next_uri'].rsplit('/', 1)[1],
                        selected_role='data_engineer')

    def settle(self, page):
        self.release.set()
        self.assertTrue(qs._pins[page['id']].initial_done.wait(3))

    def revoke(self):
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=?', (self.f.principal.principal_id,))

    def test_queued_tokens_replay_and_splice_once(self):
        page = self.submit()
        self.assertTrue(self.entered.wait(2))
        self.assertEqual((page['state'], page['backend_query_id'], page['columns'], page['data']),
                         ('QUEUED', None, [], []))
        self.assertEqual((page['request_id'], page['worksheet_id']), ('correlation', 'worksheet'))
        following = self.fetch(page)
        self.assertNotEqual(page['next_uri'], following['next_uri'])
        self.assertEqual(self.fetch(page), following)
        self.transport.assert_not_called()
        self.settle(page)
        first = self.fetch(following)
        self.assertEqual((first['id'], first['data']), (page['id'], [[42]]))
        for _ in range(20):
            self.assertEqual(self.fetch(following), first)
            self.assertEqual(self.fetch(page), following)
        self.assertEqual(self.transport.call_count, 1)
        self.transport.return_value = {'id': 'backend', 'data': [[43]]}
        self.assertEqual(self.fetch(first)['data'], [[43]])
        self.assertEqual([call.args[1] for call in self.transport.call_args_list], ['POST', 'GET'])

    def test_cancel_during_wake_and_native_pending_delete(self):
        for native in (False, True):
            # A fresh intent per case; wake's cardinality assertion is irrelevant here.
            self.lifecycle.ensure_awake = lambda **kw: (self.entered.set(), self.release.wait(3), kw['authorize']())
            self.release.clear()
            page = self.submit()
            self.assertTrue(self.entered.wait(2))
            token = page['next_uri'].rsplit('/', 1)[1]
            result = (qs._cancel_registered(self.f.principal, page['id'], token,
                selected_role='data_engineer') if native else qs.cancel(self.f.principal,
                page['id'], selected_role='data_engineer'))
            self.assertEqual(result['state'], 'CANCELED')
            self.assertEqual(qs._row(page['id'])['state'], 'CANCELED')
            self.assertIsNone(qs._pins[page['id']].owner)
            self.assertEqual(self.fetch(page)['state'], 'CANCELED')
            self.settle(page)
        self.transport.assert_not_called()
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_cached_pending_page_replay_after_cancel_is_terminal(self):
        page = self.submit()
        self.assertTrue(self.entered.wait(2))
        cached = self.fetch(page)
        self.assertEqual(cached['state'], 'QUEUED')
        self.assertIsNotNone(cached['next_uri'])
        qs.cancel(self.f.principal, page['id'], selected_role='data_engineer')
        for _ in range(3):
            replay = self.fetch(page)
            self.assertEqual((replay['state'], replay['stats']['state'], replay['next_uri']),
                             ('CANCELED', 'CANCELED', None))
        token = page['next_uri'].rsplit('/', 1)[1]
        self.assertEqual(qs._pins[page['id']].responses[token], cached)
        self.settle(page)
        self.transport.assert_not_called()

    def test_cached_pending_links_deliver_rows_after_successful_completion(self):
        self.transport.return_value = {'id': 'backend', 'data': [[42]]}
        page = self.submit()
        self.assertTrue(self.entered.wait(2))
        cached = self.fetch(page)
        self.settle(page)
        self.assertEqual(qs._row(page['id'])['state'], 'FINISHED')
        # FINISHED must not replace an old empty queued page and sever its link.
        self.assertEqual(self.fetch(page), cached)
        result = self.fetch(self.fetch(page))
        self.assertEqual((result['state'], result['data'], result['next_uri']),
                         ('FINISHED', [[42]], None))
        self.assertEqual(self.fetch(cached), result)
        self.assertEqual(self.transport.call_count, 1)

    def test_revoke_before_wake_claim(self):
        def wake(**kw):
            self.revoke()
            kw['authorize']()
        self.lifecycle.ensure_awake = wake
        page = self.submit()
        self.settle(page)
        self.assertEqual(qs._row(page['id'])['state'], 'FAILED')
        self.transport.assert_not_called()

    def test_revoke_after_readiness(self):
        # Even if core returns directly after readiness, ingress reauthorizes.
        self.lifecycle.ensure_awake = lambda **kw: (self.entered.set(), self.release.wait(3))
        page = self.submit()
        self.assertTrue(self.entered.wait(2))
        self.revoke()
        self.settle(page)
        self.assertEqual(qs._row(page['id'])['state'], 'FAILED')
        self.assertIsNone(qs._pins[page['id']].owner)
        self.transport.assert_not_called()

    def test_denied_sql_and_metadata_never_wake(self):
        self.lifecycle.ensure_awake = Mock()
        with self.assertRaises(qs.QueryError):
            qs.submit(self.f.principal, self.f.context(), 'SELECT 1; SELECT 2',
                mode='read-only', max_rows=100, request_id=None, worksheet_id=None)
        execution = self.f.begin()
        execution.inspect()
        execution.fail()
        qs.history(self.f.principal, database_id=self.f.context().database_id,
                   selected_role='data_engineer')
        self.lifecycle.ensure_awake.assert_not_called()
        self.transport.assert_not_called()

    def test_callback_failure_is_known_terminal_cached_error(self):
        self.lifecycle.ensure_awake = Mock(side_effect=RuntimeError('private failure'))
        page = self.submit()
        self.settle(page)
        for _ in range(3):
            with self.assertRaises(qs.QueryError) as caught:
                self.fetch(page)
            self.assertEqual(caught.exception.code, 'QUERY_BACKEND_FAILED')
        self.assertEqual(qs._row(page['id'])['state'], 'FAILED')
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
        self.transport.assert_not_called()

    def test_worker_startup_failure_releases_admission_and_owner(self):
        with patch.object(qs.threading.Thread, 'start', side_effect=RuntimeError('cannot start')):
            with self.assertRaises(qs.QueryError):
                self.submit()
        with control_store.connect() as db:
            query_id = db.execute('SELECT id FROM query_records').fetchone()[0]
            self.assertFalse(admission.blockers(db, 'w'))
        self.assertEqual(qs._row(query_id)['state'], 'FAILED')
        self.assertIsNone(qs._pins[query_id].owner)
        self.transport.assert_not_called()

    def test_lost_response_unknown_never_resends(self):
        self.transport.side_effect = OSError('response lost')
        page = self.submit()
        self.settle(page)
        for _ in range(5):
            with self.assertRaises(qs.QueryError):
                self.fetch(page)
        self.assertEqual(qs._row(page['id'])['state'], 'UNKNOWN')
        self.assertEqual(self.transport.call_count, 1)

    def test_restart_missing_pin_never_creates_worker(self):
        page = self.submit()
        self.settle(page)
        pin = qs._pins.pop(page['id'])
        qs._close_query_owner(pin)
        with patch.object(qs.threading, 'Thread') as worker:
            with self.assertRaises(qs.QueryError) as caught:
                self.fetch(page)
            self.assertEqual(caught.exception.code, 'QUERY_CONTEXT_LOST')
            qs.history(self.f.principal, database_id=self.f.context().database_id,
                       selected_role='data_engineer')
            worker.assert_not_called()
        self.assertEqual(self.transport.call_count, 1)

    def test_restart_loses_pending_pin_without_dispatch_or_worker_recreation(self):
        with patch.object(qs.threading, 'Thread') as worker:
            page = self.submit()
            worker.assert_called_once()
        pin = qs._pins.pop(page['id'])
        qs._close_query_owner(pin)
        self.workers.clear()  # Simulated process no longer owns any worker.
        with patch.object(qs.threading, 'Thread') as worker:
            with self.assertRaises(qs.QueryError) as caught:
                self.fetch(page)
            self.assertEqual(caught.exception.code, 'QUERY_CONTEXT_LOST')
            worker.assert_not_called()
        self.assertEqual(qs._row(page['id'])['state'], 'QUEUED')
        self.transport.assert_not_called()

    def test_cancel_after_readiness_before_tunnel_returns_never_dispatches(self):
        import warehouse_resident as wr
        from contextlib import contextmanager
        tunnel_entered, tunnel_release = threading.Event(), threading.Event()
        self.addCleanup(tunnel_release.set)
        @contextmanager
        def tunnel():
            tunnel_entered.set()
            self.assertTrue(tunnel_release.wait(3))
            yield 'http://127.0.0.1:1234'
        with patch.object(wr, 'persistent_trino', tunnel):
            page = self.submit()
            self.release.set()
            self.assertTrue(tunnel_entered.wait(2))
            self.assertEqual(qs.cancel(self.f.principal, page['id'],
                selected_role='data_engineer')['state'], 'CANCELED')
            tunnel_release.set()
            self.settle(page)
        self.transport.assert_not_called()

    def test_dispatch_wins_cancel_race_without_second_post(self):
        posted, response_release = threading.Event(), threading.Event()
        canceled = []
        def transport(url, method, pin, body=None):
            if method == 'POST':
                posted.set()
                self.assertTrue(response_release.wait(3))
                return {'id': 'backend', 'nextUri':
                        'http://127.0.0.1:1234/v1/statement/backend/1'}
            self.assertEqual(method, 'DELETE')
            return {}
        self.transport.side_effect = transport
        page = self.submit()
        self.release.set()
        self.assertTrue(posted.wait(2))
        canceler = threading.Thread(target=lambda: canceled.append(qs.cancel(
            self.f.principal, page['id'], selected_role='data_engineer')))
        canceler.start()
        response_release.set()
        canceler.join(3)
        self.assertFalse(canceler.is_alive())
        self.settle(page)
        self.assertEqual(canceled[0]['state'], 'CANCELED')
        self.assertEqual([c.args[1] for c in self.transport.call_args_list], ['POST', 'DELETE'])

    def test_dataops_wake_failure_has_known_child_and_no_retry(self):
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        self.lifecycle.ensure_awake = Mock(side_effect=RuntimeError('wake failed'))
        with self.assertRaises(RuntimeError):
            qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 42'),
                frozen, parent, execution.id, 'step')
        with control_store.connect() as db:
            query_id = db.execute('SELECT query_id FROM dataops_queries').fetchone()[0]
        self.assertEqual(qs._row(query_id)['state'], 'FAILED')
        self.assertEqual(execution._row()['committed'], 0)
        with self.assertRaises(qs.QueryError) as caught:
            qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 42'),
                frozen, parent, execution.id, 'step')
        self.assertEqual(caught.exception.code, 'STEP_ALREADY_SUBMITTED')
        self.transport.assert_not_called()
        execution.fail()

    def test_dataops_child_intent_precedes_wake_and_step_never_retries(self):
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        self.assertFalse(self.entered.is_set(), 'parent capture woke warehouse')
        def wake(**kw):
            kw['authorize']()
            with control_store.connect() as db:
                child = db.execute('SELECT * FROM dataops_queries').fetchone()
                self.assertEqual((child['operation_id'], child['step']), (execution.id, 'step'))
            self.transport.assert_not_called()
        self.lifecycle.ensure_awake = wake
        self.transport.return_value = {'id': 'child', 'data': []}
        page = qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 42'),
            frozen, parent, execution.id, 'step')
        self.assertEqual(page['request_id'], execution.id)
        with self.assertRaises(qs.QueryError) as caught:
            qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 42'),
                frozen, parent, execution.id, 'step')
        self.assertEqual(caught.exception.code, 'STEP_ALREADY_SUBMITTED')
        self.assertEqual(self.transport.call_count, 1)
        execution.complete()

    def test_warm_submit_stays_synchronous_without_worker(self):
        self.lifecycle.requires_wake = lambda: False
        self.lifecycle.ensure_awake = Mock(side_effect=lambda **kw: kw['authorize']())
        with patch.object(qs.threading, 'Thread') as worker:
            page = self.submit()
            worker.assert_not_called()
        self.assertEqual(page['backend_query_id'], 'backend')
        self.assertEqual(self.transport.call_count, 1)
        # Warm submit has no initial worker to join.
        self.workers.clear()

    def test_new_epoch_closes_live_sql_cache_and_rebinds_only_new_intents(self):
        processes, origins = [], []
        @contextmanager
        def connection():
            process = Mock()
            process.poll.return_value = None  # Old kubectl stays alive after pod loss.
            processes.append(process)
            origin = 'http://127.0.0.1:' + str(12000 + len(processes))
            origins.append(origin)
            try:
                yield dict(process=process, url=origin, kubectl=wr.KUBECTL)
            finally:
                process.terminate()
                process.wait(timeout=5)
        with ExitStack() as lifetime, patch.object(qs, '_lifetime', lifetime), \
                patch.object(wr, '_PERSISTENT_TRINO', None), \
                patch.object(wr, '_sql_kubectl', return_value=wr.KUBECTL), \
                patch.object(wr, '_trino_connection', connection), \
                patch.object(wr, 'persistent_trino', self.persistent_trino), \
                patch('logical_creations.ensure_active_provider', return_value=object()):
            old_origin = lifetime.enter_context(wr.persistent_trino())
            qs._origin = old_origin
            old_pin = qs._Pin(self.f.context(), old_origin, {}, None)
            backend_url = old_origin + '/v1/statement/old_backend/1'
            token = qs._registered(old_pin, backend_url, ('GET', 'DELETE'), 'old_backend')
            def ready(**kwargs):
                kwargs['authorize']()
                lifecycle_core._repair_provider()
                self.assertIsNone(wr._PERSISTENT_TRINO)
                processes[0].terminate.assert_called_once()
                # Another local caller may have acquired the fresh cache before
                # query_service notices its own separately cached stale origin.
                lifetime.enter_context(wr.persistent_trino())
            self.lifecycle.ensure_awake = ready
            self.transport.return_value = {'id': 'new_backend', 'data': [[42]]}
            cold = self.submit()
            self.settle(cold)
            self.assertEqual(self.fetch(cold)['data'], [[42]])
            self.assertEqual(self.transport.call_args.args[0], origins[1] + '/v1/statement')
            self.assertEqual(old_pin.origin, old_origin)
            self.assertEqual(old_pin.tokens[token][0], backend_url)
            self.assertEqual(qs._origin, origins[1])
            self.assertEqual(len(processes), 2)
            self.lifecycle.requires_wake = lambda: False
            self.lifecycle.ensure_awake = lambda **kwargs: kwargs['authorize']()
            warm = self.submit()
            self.workers.pop()  # Synchronous warm intent has no worker.
            self.assertEqual(warm['data'], [[42]])
            self.assertEqual(self.transport.call_count, 2)
            self.assertEqual([c.args[1] for c in self.transport.call_args_list], ['POST', 'POST'])
            self.assertEqual(len(processes), 2)
            processes[1].terminate.assert_not_called()
        for process in processes:
            process.terminate.assert_called_once()

    def test_dataops_new_epoch_replaces_frozen_sql_origin_without_retry(self):
        execution = self.f.begin()
        frozen, parent, _, _ = ds._live[execution.id]
        processes = []
        @contextmanager
        def connection():
            process = Mock()
            process.poll.return_value = None
            processes.append(process)
            try:
                yield dict(process=process, url='http://127.0.0.1:' + str(13000 + len(processes)),
                           kubectl=wr.KUBECTL)
            finally:
                process.terminate()
        with ExitStack() as lifetime, patch.object(qs, '_lifetime', lifetime), \
                patch.object(wr, '_PERSISTENT_TRINO', None), \
                patch.object(wr, '_sql_kubectl', return_value=wr.KUBECTL), \
                patch.object(wr, '_trino_connection', connection), \
                patch.object(wr, 'persistent_trino', self.persistent_trino), \
                patch('logical_creations.ensure_active_provider', return_value=object()):
            frozen.origin = qs._origin = lifetime.enter_context(wr.persistent_trino())
            def ready(**kwargs):
                kwargs['authorize']()
                lifecycle_core._repair_provider()
            self.lifecycle.ensure_awake = ready
            self.transport.return_value = {'id': 'child_backend', 'data': []}
            qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 42'),
                frozen, parent, execution.id, 'step')
            self.assertEqual(self.transport.call_args.args[0], 'http://127.0.0.1:13002/v1/statement')
            self.assertEqual(frozen.origin, 'http://127.0.0.1:13002')
            self.assertEqual(self.transport.call_count, 1)
            self.assertEqual(len(processes), 2)
            processes[0].terminate.assert_called_once()
            with self.assertRaises(qs.QueryError):
                qs._submit_dataops(self.f.principal, ds._Statement('select', 'SELECT 42'),
                    frozen, parent, execution.id, 'step')
            self.assertEqual(self.transport.call_count, 1)
        execution.complete()

    def test_invalidate_during_query_tunnel_selection_never_rebinds_or_reposts(self):
        checking, release_check = threading.Event(), threading.Event()
        invalidating, invalidated = threading.Event(), threading.Event()
        armed = threading.Event()
        errors = []
        process = Mock()
        class Connection(dict):
            def __getitem__(self, key):
                if key == 'url' and armed.is_set() and not invalidated.wait(3):
                    raise RuntimeError('invalidation did not finish')
                return super().__getitem__(key)
        connection = Connection(process=process, url='http://127.0.0.1:14001', kubectl=wr.KUBECTL)
        @contextmanager
        def tunnel():
            try:
                yield connection
            finally:
                process.terminate()
        def poll():
            checking.set()
            if not release_check.wait(3):
                raise RuntimeError('liveness check blocked')
            return None
        def invalidate():
            try:
                invalidating.set()
                wr.invalidate_persistent_trino()
            except BaseException as error:
                errors.append(error)
            finally:
                invalidated.set()
        self.lifecycle.ensure_awake = lambda **kwargs: kwargs['authorize']()
        self.transport.side_effect = OSError('selected tunnel closed before response')
        with ExitStack() as lifetime, patch.object(qs, '_lifetime', lifetime), \
                patch.object(wr, '_PERSISTENT_TRINO', None), \
                patch.object(wr, '_sql_kubectl', return_value=wr.KUBECTL), \
                patch.object(wr, '_trino_connection', tunnel), \
                patch.object(wr, 'persistent_trino', self.persistent_trino):
            qs._origin = lifetime.enter_context(wr.persistent_trino())
            armed.set()
            process.poll.side_effect = poll
            page = self.submit()
            invalidator = threading.Thread(target=invalidate)
            try:
                self.assertTrue(checking.wait(2))
                invalidator.start()
                self.assertTrue(invalidating.wait(2))
                self.assertFalse(invalidated.wait(.05))
            finally:
                release_check.set()
                if invalidator.ident is not None:
                    invalidator.join(4)
                self.settle(page)
            self.assertFalse(invalidator.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(qs._row(page['id'])['state'], 'UNKNOWN')
            self.assertEqual(qs._pins[page['id']].origin, 'http://127.0.0.1:14001')
            for _ in range(3):
                with self.assertRaises(qs.QueryError) as caught:
                    self.fetch(page)
                self.assertEqual(caught.exception.code, 'QUERY_BACKEND_FAILED')
            self.assertEqual(self.transport.call_count, 1)
            self.assertEqual(self.transport.call_args.args[:2],
                             ('http://127.0.0.1:14001/v1/statement', 'POST'))
            self.assertIsNone(wr._PERSISTENT_TRINO)
        process.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
