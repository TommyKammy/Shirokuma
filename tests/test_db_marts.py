"""Real durable admission/WAP boundaries with the existing unit SQL transport."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import dataops_service as ds
import db_marts as marts
import manual_wap as wap
import warehouse_admission as admission
from query_context import QueryError
import test_manual_wap


class MartTests(unittest.TestCase):

    def test_retiring_mart_parent_continues_private_check_only(self):
        mart, _ = self.create(name='retire_check')
        with patch.object(marts, '_start'):
            run = marts.submit(self.owner, self.context, mart['id'], key='retire-check',
                               expected_revision=mart['revision'], kind='check')
        self.b.retire()
        marts._execute(run['id'])
        with control_store.connect() as db:
            record = json.loads(db.execute('SELECT record FROM db_mart_runs WHERE id=?',
                                           (run['id'],)).fetchone()[0])
        self.assertEqual(record['status'], 'succeeded', record)
        self.b.assert_database_admissions()
        with self.assertRaises((QueryError, PermissionError)):
            self.observe(mart, run)

    def setUp(self):
        self.w = test_manual_wap.ManualWapTests()
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.b, self.owner = self.w.b, self.w.owner
        self.b.fixture.users['owner']['platform_admin'] = False
        self.b.fixture.save()
        self.owner = self.b.fixture.principal('owner')
        from grant_fixtures import verified_grants
        with control_store.connect() as db:
            for database in self.b.databases:
                dm.create_grant(db, self.owner.principal_id, database, 'admin')
            verified_grants(db)
        self.context = self.b.context(schema='marts', principal=self.owner, role='admin')
        synchronous = patch.object(marts, '_start', side_effect=marts._execute)
        synchronous.start()
        self.addCleanup(synchronous.stop)

    def create(self, name='daily', watch=True, source=None, model_id=None, context=None):
        context = context or self.context
        source = source or self.w.existing(database=context.database_id)
        model_id = model_id or name
        target = self.b.locator(name + '_target', context.database_id, 'marts')
        model = wap.save_model(self.w.principal, self.b.context(context.database_id), model_id=model_id,
            sql='SELECT id, label FROM source', sources={'source': source}, target=target,
            expected_version=None)
        definition = dict(name=name, model_id=model_id, version=model['version'],
            source_refs=[asdict(source)] if watch else [], target=asdict(target))
        return marts.save_mart(self.owner, context, key='save-' + name + '-' + context.database_id,
            expected_revision=0, definition=definition), source

    def refresh(self, mart, key='refresh', **options):
        current = marts.get_mart(self.owner, self.context, mart['id'])
        accepted = marts.submit(self.owner, self.context, mart['id'], key=key,
            expected_revision=current['revision'], **options)
        return self.observe(mart, accepted)

    def observe(self, mart, run):
        return marts.get_run(self.owner, self.context, mart['id'], run['id'])

    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(QueryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_first_submit_returns_frozen_ack_while_worker_holds_first_sqlite_write(self):
        connect, write_run = sqlite3.connect, marts._write_run
        for kind in ('check', 'refresh'):
            with self.subTest(kind=kind):
                mart, _ = self.create(name='ack_' + kind)
                writing, release, finished = (threading.Event() for _ in range(3))
                errors, workers = [], []

                def short_busy_connect(*args, **kwargs):
                    # Only this isolated test's connections use the short deadline.
                    kwargs['timeout'] = 0.05
                    return connect(*args, **kwargs)

                def hold_first_write(db, run):
                    write_run(db, run)
                    if not writing.is_set():
                        self.assertTrue(db.in_transaction)
                        writing.set()
                        if not release.wait(10):
                            raise AssertionError('test did not release first worker write')

                def execute(ident):
                    try:
                        marts._execute(ident)
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        finished.set()

                def start(ident):
                    worker = threading.Thread(target=execute, args=(ident,), daemon=True)
                    workers.append(worker)
                    worker.start()
                    self.assertTrue(writing.wait(5), 'worker did not reach first write')

                with patch.object(sqlite3, 'connect', side_effect=short_busy_connect), \
                        patch.object(marts, '_write_run', side_effect=hold_first_write), \
                        patch.object(marts, '_start', side_effect=start):
                    try:
                        with patch.object(marts, 'get_run', wraps=marts.get_run) as observe:
                            accepted = marts.submit(self.owner, self.context, mart['id'],
                                key='first-ack', expected_revision=1, kind=kind)
                            observe.assert_not_called()
                        self.assertTrue(writing.is_set())
                        self.assertFalse(release.is_set())
                        self.assertFalse(finished.is_set())
                        self.assertEqual(accepted['status'], 'queued')
                        self.assertEqual(accepted['context'], asdict(self.context))
                        self.assertEqual(accepted['owner'], self.owner.principal_id)
                        self.assertFalse(any(k.startswith('_') for k in accepted))
                        frozen = json.loads(json.dumps(accepted))
                        with control_store.connect() as db:
                            durable = marts._run_row(db, accepted['id'])[1]
                            self.assertEqual(accepted, marts._public_run(durable))
                            self.assertIn('_credential_snapshot_ref', durable)
                        # Demonstrate the former post-start observation really
                        # contends on this SQLite writer, rather than mocking it.
                        with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                            self.observe(mart, accepted)
                    finally:
                        release.set()
                        for worker in workers:
                            worker.join(10)
                            self.assertFalse(worker.is_alive(), 'worker did not finish')
                    self.assertTrue(finished.is_set())
                    self.assertEqual(errors, [])
                    self.assertEqual(accepted, frozen)
                    observed = self.observe(mart, accepted)
                    self.assertEqual(observed['status'], 'succeeded')
                    self.assertEqual(observed['id'], accepted['id'])
                    replay = marts.submit(self.owner, self.context, mart['id'],
                        key='first-ack', expected_revision=1, kind=kind)
                    self.assertEqual(replay, observed)
                    self.assertFalse(any(k.startswith('_') for k in observed))
                    with control_store.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM db_mart_runs WHERE mart_id=?',
                            (mart['id'],)).fetchone()[0], 1)
                        requests = db.execute('SELECT response FROM db_mart_requests WHERE route=?',
                            ('/api/v1/marts/' + mart['id'] + ('/checks' if kind == 'check' else '/refreshes'),)).fetchall()
                        self.assertEqual([json.loads(r['response']) for r in requests],
                            [dict(run_id=accepted['id'])])
                        self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='mart' AND subject_id=?",
                            (accepted['id'],)).fetchone()[0], 'FINISHED')

    def test_p3_published_locator_registers_new_mart_from_public_model_read_without_sql(self):
        built, audited = self.w.audited()
        receipt = wap.publish(self.owner, self.context, key='publish-before-registration',
            build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(receipt['state'], 'FINISHED', receipt)
        publication = receipt['result']['publication']
        count = len(self.w.posts)
        model = wap.get_model(self.owner, self.context, model_id=built['model_id'], version=1)
        self.assertEqual(model['target_kind'], 'locator')
        self.assertEqual(model['current_target_ref'], publication['target']['ref'])
        definition = dict(name='already-published', model_id=model['model_id'], version=model['version'],
            source_refs=list(model['sources'].values()), target=model['current_target_ref'])
        mart = marts.save_mart(self.owner, self.context, key='register-published',
            expected_revision=0, definition=definition)
        self.assertEqual(mart['definition']['target'], publication['target']['ref'])
        self.assertEqual(mart['definition']['model_id'], model['model_id'])
        self.assertEqual(mart['definition']['version'], 1)
        self.assertEqual(len(self.w.posts), count)

    def test_full_refresh_baseline_and_unchanged_check_never_change_success_time(self):
        mart, source = self.create()
        self.assertEqual(self.w.posts, [])
        first = self.refresh(mart)
        self.assertEqual(first['status'], 'succeeded', first)
        self.assertTrue(first['committed'])
        self.assertEqual(len(self.w.posts), 3)
        saved = marts.get_mart(self.owner, self.context, mart['id'])
        self.assertEqual(saved['current_target_ref'], saved['definition']['target'])
        self.assertEqual(saved['revision'], 2)
        baseline = saved['last_success_baseline']
        self.assertEqual(baseline['comparison']['sources'][0]['snapshot'], str(self.w.metadata(source)['current-snapshot-id']))
        check = self.refresh(mart, 'check', kind='check')
        self.assertEqual(check['result'], dict(changed=False, unchanged=True))
        skipped = self.refresh(mart, 'conditional', check_changes=True)
        self.assertEqual(skipped['status'], 'skipped')
        self.assertFalse(skipped['committed'])
        self.assertEqual(len(self.w.posts), 3)
        self.assertEqual(marts.get_mart(self.owner, self.context, mart['id'])['last_success_at'], saved['last_success_at'])
        unconditional = self.refresh(mart, 'again')
        self.assertEqual(unconditional['status'], 'succeeded', unconditional)
        self.assertEqual(len(self.w.posts), 6)

    def test_start_capture_pinned_even_if_later_head_appends(self):
        mart, source = self.create()
        snapshot = str(self.w.metadata(source)['current-snapshot-id'])
        build = wap.build
        def append_then_build(*args, **kwargs):
            self.w.snapshot(self.w.metadata(source), [(91, 'later')])
            return build(*args, **kwargs)
        with patch.object(wap, 'build', side_effect=append_then_build):
            run = self.refresh(mart)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertIn('FOR VERSION AS OF ' + snapshot, self.w.posts[0][0])
        self.assertEqual(run['publication']['target']['snapshot'], str(self.w.sequence))
        baseline = marts.get_mart(self.owner, self.context, mart['id'])['last_success_baseline']
        self.assertEqual(baseline['comparison']['sources'][0]['snapshot'], snapshot)
        self.assertTrue(self.refresh(mart, 'check', kind='check')['result']['changed'])

    def test_self_join_captures_each_ref_once_and_does_not_consume_append(self):
        source = self.w.existing()
        target = self.b.locator('joined_target', schema='marts')
        model = wap.save_model(self.w.principal, self.b.context(), model_id='joined',
            sql='SELECT a.id, a.label FROM a JOIN b ON a.id=b.id',
            sources={'a': source, 'b': source}, target=target, expected_version=None)
        mart = marts.save_mart(self.owner, self.context, key='join', expected_revision=0,
            definition=dict(name='joined', model_id='joined', version=model['version'],
                source_refs=[asdict(source)], target=asdict(target)))
        snapshot = str(self.w.metadata(source)['current-snapshot-id'])
        measure, comparison = wap._Metadata.wap_measure, marts._comparison
        captures = []
        def append_after_measure(metadata, ref):
            value = measure(metadata, ref)
            if ref == source:
                captures.append(value)
                if len(captures) == 1:
                    self.w.snapshot(self.w.metadata(source), [(91, 'later')])
            return value
        def compare(model, measurements):
            self.assertEqual(len(captures), 1)
            self.assertIs(measurements['a'], measurements['b'])
            return comparison(model, measurements)
        with patch.object(wap._Metadata, 'wap_measure', new=append_after_measure), \
                patch.object(marts, '_comparison', side_effect=compare):
            run = self.refresh(mart)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual(run['start_measurements']['a'], run['start_measurements']['b'])
        self.assertEqual(self.w.posts[0][0].count('FOR VERSION AS OF ' + snapshot), 2)
        self.assertEqual(run['comparison']['sources'], [dict(ref=asdict(source), snapshot=snapshot)])
        self.assertTrue(self.refresh(mart, 'after-append', kind='check')['result']['changed'])
        self.assertEqual(len(self.w.posts), 3)

    def advance_context(self, *, binding=False):
        from query_context import resolve_context
        previous = self.context
        generation = 'next-' + previous.generation_id
        revision = previous.binding_revision + int(binding)
        with control_store.connect() as db:
            dm.record_generation(db, dm.WarehouseConfigGeneration(generation, previous.warehouse_id,
                previous.database_id, (), (), (), 'READY'))
            db.execute('UPDATE catalog_bindings SET desired_revision=?,applied_revision=?,applied_generation_id=? '
                'WHERE database_id=?', (revision, revision, generation, previous.database_id))
            db.execute('UPDATE effective_grants SET epoch=epoch+1,applied_revision=?,applied_generation_id=? '
                'WHERE principal_id=? AND database_id=?',
                (revision, generation, self.owner.principal_id, previous.database_id))
        self.context = resolve_context(self.owner, previous.database_id, previous.schema_id, 'admin',
            warehouse_id=previous.warehouse_id, expected_binding_revision=revision)
        # The shared unit transport's default prepare function pins its initial
        # generation. Resolve the real current binding for transition tests.
        import request_auth
        import trino_auth
        def prepare(*, principal, database_id, selected_role):
            with control_store.connect() as db:
                generation = dm.get_binding(db, database_id).applied_generation_id
            decision = request_auth.authorize_database(principal, database_id, selected_role,
                expected_generation_id=generation)
            return decision, {'X-Trino-User': 'fixed_' + database_id, 'Authorization': self.b.credential}
        patcher = patch.object(trino_auth, 'prepare_backend_v2', side_effect=prepare)
        patcher.start()
        self.addCleanup(patcher.stop)
        return previous

    def test_history_selects_current_full9_after_real_generation_transition(self):
        mart, _ = self.create()
        old = self.refresh(mart)
        previous = self.advance_context()
        self.assertNotEqual(previous.generation_id, self.context.generation_id)
        self.assertNotEqual(previous.grant_revision, self.context.grant_revision)
        self.assertEqual(marts.list_runs(self.owner, self.context, mart['id']), [])
        self.assert_code('CONTEXT_MISMATCH', self.observe, mart, old)
        fresh = self.refresh(mart, 'fresh-generation', kind='check')
        self.assertEqual(fresh['status'], 'succeeded', fresh)
        self.assertEqual([r['id'] for r in marts.list_runs(self.owner, self.context, mart['id'])], [fresh['id']])
        with control_store.connect() as db:
            self.assertEqual(marts._run_row(db, old['id'])[1]['context'], asdict(previous))

    def downgrade(self, revoke=False):
        with control_store.connect() as db:
            if revoke:
                db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                    (self.owner.principal_id, self.context.database_id))
            else:
                db.execute("UPDATE effective_grants SET maximum_role='data_engineer' WHERE principal_id=? AND database_id=?",
                    (self.owner.principal_id, self.context.database_id))

    def test_observe_denies_downgrade_after_settlement_before_return(self):
        mart, _ = self.create()
        with patch.object(marts, '_finalize', side_effect=OSError('history')):
            run = self.refresh(mart)
        count = len(self.w.posts)
        settle = marts._settle
        def settle_then_downgrade(*args):
            settle(*args)
            self.downgrade()
        with patch.object(marts, '_settle', side_effect=settle_then_downgrade):
            self.assert_code('DB_ACCESS_REVOKED', self.observe, mart, run)
        self.assertEqual(len(self.w.posts), count)
        with self.assertRaises(QueryError):
            marts.get_mart(self.owner, self.context, mart['id'])

    def test_both_submit_replays_revalidate_before_return_without_sql(self):
        mart, _ = self.create()
        self.refresh(mart)
        count = len(self.w.posts)
        request = marts._request
        for second in (False, True):
            with self.subTest(second=second):
                calls = 0
                def replay_then_downgrade(*args):
                    nonlocal calls
                    calls += 1
                    if second and calls == 1:
                        return None
                    result = request(*args)
                    if result:
                        # The second lookup holds the write transaction. Change
                        # authority on that same connection before its return.
                        args[0].execute("UPDATE effective_grants SET maximum_role='data_engineer' "
                            'WHERE principal_id=? AND database_id=?',
                            (self.owner.principal_id, self.context.database_id))
                    return result
                with patch.object(marts, '_request', side_effect=replay_then_downgrade):
                    with self.assertRaises(QueryError):
                        marts.submit(self.owner, self.context, mart['id'], key='refresh', expected_revision=1)
                with control_store.connect() as db:
                    db.execute("UPDATE effective_grants SET maximum_role='admin' WHERE principal_id=? AND database_id=?",
                        (self.owner.principal_id, self.context.database_id))
                self.assertEqual(calls, 2 if second else 1)
        self.assertEqual(len(self.w.posts), count)

    def test_first_ack_revalidates_authority_after_capture_before_admission(self):
        import query_service
        mart, _ = self.create()
        capture = query_service._capture_dataops
        def capture_then_revoke(*args):
            frozen = capture(*args)
            self.downgrade(revoke=True)
            return frozen
        with patch.object(query_service, '_capture_dataops', side_effect=capture_then_revoke), \
                patch.object(marts, '_start') as start:
            self.assert_code('DB_ACCESS_REVOKED', marts.submit, self.owner, self.context,
                mart['id'], key='revoked-before-admission', expected_revision=1)
            start.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_mart_runs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM db_mart_requests WHERE idempotency_key=?',
                ('revoked-before-admission',)).fetchone()[0], 0)
        self.assertEqual(self.w.posts, [])

    def test_revoked_grant_blocks_submit_replay_and_observe_without_sql(self):
        mart, _ = self.create()
        run = self.refresh(mart)
        count = len(self.w.posts)
        self.downgrade(revoke=True)
        self.assert_code('DB_ACCESS_REVOKED', marts.submit, self.owner, self.context,
            mart['id'], key='refresh', expected_revision=1)
        self.assert_code('DB_ACCESS_REVOKED', self.observe, mart, run)
        self.assertEqual(len(self.w.posts), count)

    def test_list_denies_revocation_after_last_run_observation(self):
        mart, _ = self.create()
        self.refresh(mart)
        count = len(self.w.posts)
        get_run = marts.get_run
        def observe_then_revoke(*args):
            result = get_run(*args)
            self.downgrade(revoke=True)
            return result
        with patch.object(marts, 'get_run', side_effect=observe_then_revoke):
            self.assert_code('DB_ACCESS_REVOKED', marts.list_runs, self.owner, self.context, mart['id'])
        self.assertEqual(len(self.w.posts), count)

    def test_mart_summary_reads_revalidate_after_initial_access(self):
        mart, _ = self.create()
        access = marts._access
        def access_then_downgrade(*args):
            result = access(*args)
            self.downgrade()
            return result
        for action, args in ((marts.get_mart, (mart['id'],)), (marts.list_marts, ())):
            with self.subTest(action=action.__name__):
                with patch.object(marts, '_access', side_effect=access_then_downgrade):
                    self.assert_code('DB_ACCESS_REVOKED', action, self.owner, self.context, *args)
                with control_store.connect() as db:
                    db.execute("UPDATE effective_grants SET maximum_role='admin' WHERE principal_id=? AND database_id=?",
                        (self.owner.principal_id, self.context.database_id))
        self.assertEqual(self.w.posts, [])

    def test_observe_denies_revoke_after_settlement_before_return(self):
        mart, _ = self.create()
        with patch.object(marts, '_finalize', side_effect=OSError('history')):
            run = self.refresh(mart)
        count = len(self.w.posts)
        settle = marts._settle
        def settle_then_revoke(*args):
            settle(*args)
            self.downgrade(revoke=True)
        with patch.object(marts, '_settle', side_effect=settle_then_revoke):
            self.assert_code('DB_ACCESS_REVOKED', self.observe, mart, run)
        self.assertEqual(len(self.w.posts), count)

    def test_current_owner_repairs_closed_history_after_binding_refresh(self):
        mart, _ = self.create()
        with patch.object(marts, '_finalize', side_effect=OSError('history')):
            run = self.refresh(mart)
            saved = marts.get_mart(self.owner, self.context, mart['id'])
        self.assertTrue(run['baseline_saved'])
        previous = self.advance_context(binding=True)
        count = len(self.w.posts)
        self.assert_code('STALE_BINDING', marts.get_run, self.owner, previous, mart['id'], run['id'])
        self.assert_code('CONTEXT_MISMATCH', self.observe, mart, run)
        repaired = marts.get_mart(self.owner, self.context, mart['id'])
        self.assertFalse(repaired['reconciliation_required'], repaired)
        self.assertEqual(repaired['last_success_at'], saved['last_success_at'])
        self.assertEqual(repaired['last_success_baseline'], saved['last_success_baseline'])
        self.assertEqual(len(self.w.posts), count)
        self.assertEqual(marts.list_runs(self.owner, self.context, mart['id']), [])
        self.assert_code('CONTEXT_MISMATCH', self.observe, mart, run)
        with control_store.connect() as db:
            stored = marts._run_row(db, run['id'])[1]
            self.assertEqual(stored['context'], asdict(previous))
            self.assertEqual(stored['status'], 'succeeded')

    def test_closed_repair_rejects_foreign_owner_and_revoked_or_downgraded_owner(self):
        mart, _ = self.create()
        with patch.object(marts, '_finalize', side_effect=OSError('history')):
            run = self.refresh(mart)
        self.advance_context(binding=True)
        count = len(self.w.posts)
        with patch.object(marts, '_publication', wraps=marts._publication) as proof:
            self.b.fixture.users['foreign'] = dict(self.b.fixture.users['owner'], platform_admin=True)
            self.b.fixture.save()
            foreign = self.b.fixture.principal('foreign')
            self.assert_code('MART_NOT_FOUND', marts.get_mart, foreign,
                replace(self.context, grant_revision=None), mart['id'])
            self.downgrade()
            with self.assertRaises(QueryError):
                marts.get_mart(self.owner, self.context, mart['id'])
            self.downgrade(revoke=True)
            with self.assertRaises(QueryError):
                marts.get_mart(self.owner, self.context, mart['id'])
            proof.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(marts._run_row(db, run['id'])[1]['status'], 'reconciliation_required')
        self.assertEqual(len(self.w.posts), count)

    def test_current_context_get_mart_does_not_repair_true_sql_unknown(self):
        import query_service
        mart, _ = self.create()
        with patch.object(query_service, '_transport', side_effect=TimeoutError('response lost')):
            run = self.refresh(mart)
        self.advance_context(binding=True)
        count = len(self.w.posts)
        with patch.object(marts, '_publication', wraps=marts._publication) as proof:
            saved = marts.get_mart(self.owner, self.context, mart['id'])
            self.assertTrue(saved['reconciliation_required'])
            proof.assert_not_called()
        self.assert_code('MART_RECONCILIATION_REQUIRED', self.refresh, mart, 'still-blocked')
        with control_store.connect() as db:
            self.assertEqual(marts._run_row(db, run['id'])[1]['status'], 'unknown')
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='mart' AND subject_id=?",
                (run['id'],)).fetchone()[0], 'UNKNOWN')
        self.assertEqual(len(self.w.posts), count)

    def test_lower_snapshot_rollback_and_model_identity_are_changes(self):
        mart, source = self.create()
        metadata = self.w.metadata(source)
        old = metadata['current-snapshot-id']
        self.w.snapshot(metadata, [(8, 'new')])
        self.assertEqual(self.refresh(mart)['status'], 'succeeded')
        metadata['current-snapshot-id'] = old
        self.assertTrue(self.refresh(mart, 'rolled', kind='check')['result']['changed'])
        self.assertEqual(self.refresh(mart, 'rollback-refresh')['status'], 'succeeded')
        saved = marts.get_mart(self.owner, self.context, mart['id'])
        target = dm.ResourceRef(**saved['current_target_ref'])
        for model_id, expected in [('daily', 1), ('other-model', None)]:
            model = wap.save_model(self.w.principal, self.b.context(), model_id=model_id,
                sql='SELECT id FROM source', sources={'source': source}, target=target, expected_version=expected)
            definition = dict(saved['definition'], model_id=model_id, version=model['version'])
            saved = marts.save_mart(self.owner, self.context, mart_id=saved['id'], key='update-' + model_id,
                expected_revision=saved['revision'], definition=definition)
            self.assertTrue(self.refresh(saved, 'check-' + model_id, kind='check')['result']['changed'])

    def test_manual_only_and_constant_query(self):
        mart, _ = self.create(watch=False)
        self.assert_code('WATCH_REQUIRED', self.refresh, mart, kind='check')
        self.assert_code('WATCH_REQUIRED', self.refresh, mart, check_changes=True)
        run = self.refresh(mart)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual(set(run['start_measurements']), {'source'})
        target = self.b.locator('constant', schema='marts')
        wap.save_model(self.w.principal, self.b.context(), model_id='constant', sql='SELECT 1 AS id',
            sources={}, target=target, expected_version=None)
        constant = marts.save_mart(self.owner, self.context, key='constant', expected_revision=0,
            definition=dict(name='constant', model_id='constant', version=1, source_refs=[], target=asdict(target)))
        self.assertEqual(self.refresh(constant, 'constant')['status'], 'succeeded')

    def test_owner_three_database_role_and_full_context_isolation(self):
        mart, _ = self.create()
        self.b.fixture.users['another_admin'] = dict(self.b.fixture.users['owner'], platform_admin=True)
        self.b.fixture.save()
        other_owner = self.b.fixture.principal('another_admin')
        other_context = self.b.context(schema='marts', principal=other_owner, role='admin')
        self.assert_code('MART_NOT_FOUND', marts.get_mart, other_owner, other_context, mart['id'])
        self.assertEqual(marts.list_marts(other_owner, other_context), [])
        for database in self.b.databases[1:]:
            context = self.b.context(database, schema='marts', principal=self.owner, role='admin')
            other, _ = self.create(context=context)
            self.assertNotEqual(mart['id'], other['id'])
            self.assertEqual(len(marts.list_marts(self.owner, context)), 1)
            self.assert_code('MART_NOT_FOUND', marts.get_mart, self.owner, context, mart['id'])
        self.assert_code('SQL_PERMISSION_DENIED', marts.get_mart, self.w.principal,
            self.b.context(schema='marts'), mart['id'])
        self.assert_code('STALE_BINDING', marts.get_mart, self.owner,
            replace(self.context, binding_revision=99), mart['id'])
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                (self.owner.principal_id, self.context.database_id))
        with self.assertRaises(QueryError):
            marts.get_mart(self.owner, self.context, mart['id'])

    def test_cas_idempotency_no_sql_on_registration_and_replay(self):
        mart, _ = self.create()
        save_key = 'save-daily-' + self.context.database_id
        saved = marts.save_mart(self.owner, self.context, key=save_key, expected_revision=0, definition=mart['definition'])
        self.assertEqual(saved['id'], mart['id'])
        self.assert_code('IDEMPOTENCY_CONFLICT', marts.save_mart, self.owner, self.context,
            key=save_key, expected_revision=0, definition=dict(mart['definition'], name='changed'))
        self.assert_code('MART_REVISION_CONFLICT', marts.save_mart, self.owner, self.context,
            key='update', mart_id=mart['id'], expected_revision=9, definition=mart['definition'])
        self.assertEqual(self.w.posts, [])
        run = self.refresh(mart)
        replay = marts.submit(self.owner, self.context, mart['id'], key='refresh', expected_revision=1)
        self.assertEqual(run['id'], replay['id'])
        self.assertEqual(len(self.w.posts), 3)
        self.assert_code('IDEMPOTENCY_CONFLICT', marts.submit, self.owner, self.context, mart['id'],
            key='refresh', expected_revision=1, check_changes=True)

    def test_watch_exact_set_and_existing_locator_no_adoption(self):
        mart, _ = self.create()
        another = self.w.existing('other')
        self.assert_code('WATCH_SOURCES_MISMATCH', marts.save_mart, self.owner, self.context,
            key='wrong-watch', mart_id=mart['id'], expected_revision=1,
            definition=dict(mart['definition'], source_refs=[asdict(another)]))
        self.w.existing('daily_target', schema='marts')
        run = self.refresh(mart)
        self.assertEqual(run['status'], 'failed', run)
        self.assertFalse(run['committed'])
        self.assertEqual(self.w.posts, [])

    def test_published_target_updates_require_exact_current_ref(self):
        mart, _ = self.create()
        self.assertEqual(self.refresh(mart)['status'], 'succeeded')
        saved = marts.get_mart(self.owner, self.context, mart['id'])
        self.assert_code('MATERIALIZED_TARGET_REQUIRED', marts.save_mart, self.owner, self.context,
            key='old-locator', mart_id=mart['id'], expected_revision=saved['revision'], definition=mart['definition'])

    def test_explicit_existing_target_and_duplicate_watches(self):
        source = self.w.existing()
        target = self.w.existing('existing_public', schema='marts', rows=[(0, 'old')])
        wap.save_model(self.w.principal, self.b.context(), model_id='explicit-existing',
            sql='SELECT id FROM source', sources={'source': source}, target=target, expected_version=None)
        mart = marts.save_mart(self.owner, self.context, key='explicit-existing', expected_revision=0,
            definition=dict(name='explicit-existing', model_id='explicit-existing', version=1,
                source_refs=[asdict(source), asdict(source)], target=asdict(target)))
        self.assertEqual(len(mart['definition']['source_refs']), 1)
        run = self.refresh(mart)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual(run['publication']['target']['ref'], asdict(target))
        self.assertEqual(marts.get_mart(self.owner, self.context, mart['id'])['revision'], 1)

    def test_missing_current_snapshot_is_failure_not_unchanged(self):
        mart, source = self.create()
        self.assertEqual(self.refresh(mart)['status'], 'succeeded')
        before = marts.get_mart(self.owner, self.context, mart['id'])
        self.w.metadata(source).pop('current-snapshot-id')
        count = len(self.w.posts)
        run = self.refresh(mart, 'missing-head', kind='check')
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['error'], 'SNAPSHOT_REQUIRED')
        self.assertEqual(len(self.w.posts), count)
        self.assertEqual(marts.get_mart(self.owner, self.context, mart['id'])['last_success_baseline'], before['last_success_baseline'])

    def test_model_change_between_capture_and_build_never_submits(self):
        mart, source = self.create()
        build = wap.build
        def change(*args, **kwargs):
            wap.save_model(self.w.principal, self.b.context(), model_id='daily', sql='SELECT id FROM source',
                sources={'source': source}, target=self.b.locator('daily_target', schema='marts'), expected_version=1)
            return build(*args, **kwargs)
        with patch.object(wap, 'build', side_effect=change):
            run = self.refresh(mart)
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['error'], 'MODEL_VERSION_CHANGED')
        self.assertEqual(self.w.posts, [])

    def test_raw_pin_cannot_enter_standalone_p3(self):
        source = self.w.existing()
        self.w.save(source=source)
        self.assert_code('MART_CONTINUATION_REQUIRED', wap.build, self.w.principal, self.b.context(),
            key='forged-pin', model_id='shared', version=1, _start='caller-input')
        self.assertEqual(self.w.posts, [])

    def test_all_full9_fields_are_bound_on_observe(self):
        mart, _ = self.create()
        run = self.refresh(mart)
        for context in (replace(self.context, generation_id='old'),
                        replace(self.context, grant_revision=self.context.grant_revision + 1),
                        replace(self.context, sql_catalog='another')):
            self.assert_code('CONTEXT_MISMATCH', marts.get_run, self.owner, context, mart['id'], run['id'])
        # A newly effective epoch can execute new work but cannot expose the old
        # run through a list bound to its new full9 context.
        with control_store.connect() as db:
            db.execute('UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id=? AND database_id=?',
                (self.owner.principal_id, self.context.database_id))
        current = self.b.context(schema='marts', principal=self.owner, role='admin')
        self.assertEqual(marts.list_runs(self.owner, current, mart['id']), [])
        self.assert_code('CONTEXT_MISMATCH', marts.get_run, self.owner, current, mart['id'], run['id'])

    def test_source_schema_drift_after_capture_fails_before_build_sql(self):
        mart, source = self.create()
        build = wap.build
        def drift(*args, **kwargs):
            self.w.metadata(source)['schemas'][0]['fields'][0]['required'] = True
            return build(*args, **kwargs)
        with patch.object(wap, 'build', side_effect=drift):
            run = self.refresh(mart)
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['error'], 'SOURCE_SCHEMA_CHANGED')
        self.assertEqual(self.w.posts, [])

    def test_source_recreation_after_audit_preserves_previous_publication(self):
        mart, _ = self.create()
        self.assertEqual(self.refresh(mart)['status'], 'succeeded')
        previous = marts.get_mart(self.owner, self.context, mart['id'])
        audit = wap.audit
        def recreate(*args, **kwargs):
            result = audit(*args, **kwargs)
            self.w.existing()
            return result
        with patch.object(wap, 'audit', side_effect=recreate):
            run = self.refresh(mart, 'recreated-after-audit')
        self.assertEqual(run['status'], 'failed', run)
        self.assertFalse(run['committed'])
        self.assertEqual(len(self.w.posts), 5)
        self.assertEqual(marts.get_mart(self.owner, self.context, mart['id'])['last_success_baseline'], previous['last_success_baseline'])

    def test_quality_failure_preserves_baseline_and_public_target(self):
        mart, source = self.create()
        first = self.refresh(mart)
        saved = marts.get_mart(self.owner, self.context, mart['id'])
        self.w.snapshot(self.w.metadata(source), [])
        failed = self.refresh(mart, 'empty')
        self.assertEqual(failed['error'], 'QUALITY_FAILED')
        self.assertFalse(failed['committed'])
        self.assertEqual(failed['status'], 'failed')
        current = marts.get_mart(self.owner, self.context, mart['id'])
        self.assertEqual(current['last_success_baseline'], saved['last_success_baseline'])
        self.assertEqual(self.w.metadata(dm.ResourceRef(**current['current_target_ref']))['current-snapshot-id'],
            int(first['publication']['target']['snapshot']))

    def test_source_and_target_recreation_fail_before_sql(self):
        for target in (False, True):
            with self.subTest(target=target):
                mart, source = self.create(name='recreate' + str(int(target)))
                self.assertEqual(self.refresh(mart, 'first-' + mart['id'])['status'], 'succeeded')
                saved = marts.get_mart(self.owner, self.context, mart['id'])
                if target:
                    self.w.existing('recreate1_target', schema='marts')
                else:
                    self.w.existing()
                count = len(self.w.posts)
                run = self.refresh(mart, 'recreated-' + mart['id'])
                self.assertEqual(run['status'], 'failed', run)
                self.assertEqual(len(self.w.posts), count)
                self.assertEqual(marts.get_mart(self.owner, self.context, mart['id'])['last_success_baseline'], saved['last_success_baseline'])

    def test_stage_drift_after_audit_and_grant_loss_before_publish(self):
        for revoke in (False, True):
            with self.subTest(revoke=revoke):
                mart, _ = self.create(name='drift' + str(int(revoke)))
                audit = wap.audit
                def drift(*args, **kwargs):
                    result = audit(*args, **kwargs)
                    if revoke:
                        with control_store.connect() as db:
                            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                                (self.owner.principal_id, self.context.database_id))
                    else:
                        ref = dm.ResourceRef(**result['result']['audit']['stage']['ref'])
                        self.w.snapshot(self.w.metadata(ref), [(55, 'drift')])
                    return result
                with patch.object(wap, 'audit', side_effect=drift):
                    if revoke:
                        accepted = marts.submit(self.owner, self.context, mart['id'],
                            key='refresh-' + mart['id'], expected_revision=mart['revision'])
                        self.assertEqual(accepted['status'], 'queued')
                        with self.assertRaises(QueryError):
                            self.observe(mart, accepted)
                    else:
                        run = self.refresh(mart, 'refresh-' + mart['id'])
                        self.assertEqual(run['status'], 'failed', run)
                        self.assertFalse(run['committed'])
                self.assertFalse(any('CREATE TABLE "sales"."marts"' in p[0] for p in self.w.posts))

    def test_artifact_failure_reconciles_positive_receipt_without_resend(self):
        mart, _ = self.create()
        store = wap._store_artifact
        def fail_publication(table, record):
            if table == 'wap_publications':
                raise OSError('artifact unavailable')
            return store(table, record)
        with patch.object(wap, '_store_artifact', side_effect=fail_publication):
            run = self.refresh(mart)
        self.assertEqual(run['status'], 'reconciliation_required', run)
        self.assertTrue(run['committed'])
        count = len(self.w.posts)
        observed = self.observe(mart, run)
        self.assertEqual(observed['status'], 'succeeded', observed)
        self.assertEqual(len(self.w.posts), count)

    def test_baseline_and_history_failures_block_only_same_mart(self):
        for phase in ('_save_baseline', '_finalize'):
            with self.subTest(phase=phase):
                mart, _ = self.create(name=phase.strip('_'))
                original = getattr(marts, phase)
                def fail_this(ident, publication):
                    with control_store.connect() as db:
                        _, value = marts._run_row(db, ident)
                    if value['mart_id'] == mart['id']:
                        raise OSError('bookkeeping')
                    return original(ident, publication)
                with patch.object(marts, phase, side_effect=fail_this):
                    run = self.refresh(mart, key=phase)
                    saved = marts.get_mart(self.owner, self.context, mart['id'])
                    self.assertEqual(run['status'], 'reconciliation_required', run)
                    self.assertEqual(saved['last_success_baseline'] is not None, phase == '_finalize')
                    self.assert_code('MART_RECONCILIATION_REQUIRED', self.refresh, mart, 'blocked-' + phase)
                    other, _ = self.create(name='other' + phase)
                    self.assertEqual(self.refresh(other, 'other-' + phase)['status'], 'succeeded')
                count = len(self.w.posts)
                repaired = self.observe(mart, run)
                self.assertEqual(repaired['status'], 'succeeded', repaired)
                self.assertEqual(len(self.w.posts), count)
                if phase == '_finalize':
                    self.assertEqual(saved['last_success_at'], marts.get_mart(self.owner, self.context, mart['id'])['last_success_at'])

    def test_insufficient_publication_proof_blocks_mart_but_releases_capacity(self):
        mart, _ = self.create()
        receipt = wap._receipt_intent
        store = wap._store_artifact
        def lose_proof(execution, result):
            if 'publication' not in result:
                receipt(execution, result)
        def lose_artifact(table, record):
            if table == 'wap_publications':
                raise OSError('lost artifact')
            store(table, record)
        with patch.object(wap, '_receipt_intent', side_effect=lose_proof), patch.object(wap, '_store_artifact', side_effect=lose_artifact):
            run = self.refresh(mart)
        self.assertEqual(run['status'], 'reconciliation_required', run)
        self.assertTrue(run['committed'])
        self.assertIsNone(marts.get_mart(self.owner, self.context, mart['id'])['last_success_baseline'])
        count = len(self.w.posts)
        self.assertEqual(self.observe(mart, run)['status'], 'reconciliation_required')
        self.assertEqual(len(self.w.posts), count)
        other, _ = self.create(name='usable')
        self.assertEqual(self.refresh(other, 'usable')['status'], 'succeeded')

    def test_unknown_sql_holds_root_restart_and_replay_never_resend(self):
        mart, _ = self.create()
        with patch.object(self.w, 'transport', side_effect=TimeoutError('response lost')):
            # Existing transport patch dispatches through its bound side effect;
            # replace the actual transport boundary for this fault.
            import query_service
            with patch.object(query_service, '_transport', side_effect=TimeoutError('response lost')):
                run = self.refresh(mart)
        self.assertEqual(run['status'], 'unknown', run)
        self.assertFalse(run['committed'])
        with control_store.connect() as db:
            root = db.execute("SELECT state FROM warehouse_admissions WHERE kind='mart' AND subject_id=?", (run['id'],)).fetchone()
            self.assertEqual(root['state'], 'UNKNOWN')
        for live in ds._live.values():
            live[2].close()
        ds._live.clear()
        self.assertEqual(self.observe(mart, run)['status'], 'unknown')
        other, _ = self.create(name='held')
        with self.assertRaises(admission.AdmissionConflict):
            self.refresh(other, 'held')
        replay = marts.submit(self.owner, self.context, mart['id'], key='refresh', expected_revision=1)
        self.assertEqual(replay['id'], run['id'])

    def test_atomic_child_link_rollback_and_cross_process_capacity(self):
        mart, _ = self.create()
        with patch.object(marts, '_start'):
            run = self.refresh(mart)
        principal, context, parent, frozen, credential, owner = marts._live[run['id']]
        self.addCleanup(lambda: marts._live.pop(run['id'], None))
        self.addCleanup(owner.close)
        with control_store.connect() as db:
            _, record = marts._run_row(db, run['id'])
            record['status'] = 'running'
            marts._write_run(db, record)
        with ds._mart_scope(principal, context, parent, frozen, credential, run['id'], 'build'):
            with self.assertRaises(RuntimeError):
                ds.begin(principal, context, route='atomic-mart', idempotency_key='atomic', payload={},
                    _admission_hook=lambda db, ident: (_ for _ in ()).throw(RuntimeError('rollback')))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_mart_operations').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM dataops_operations WHERE route='atomic-mart'").fetchone()[0], 0)
            for kind in ('job', 'stage_scan', 'dataops', 'mart'):
                with self.subTest(kind=kind), self.assertRaises(admission.AdmissionConflict):
                    if not db.in_transaction:
                        db.execute('BEGIN IMMEDIATE')
                    admission.admit(db, warehouse_id='w', actor=self.owner.principal_id, kind=kind, subject_id='blocked')
                    db.rollback()
        code = 'import control_store as c, warehouse_admission as a\nwith c.connect() as d:\n d.execute("BEGIN IMMEDIATE")\n a.admit(d,warehouse_id="w",actor="other",kind="mart",subject_id="other")'
        result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PYTHONPATH=str(Path(ds.__file__).parent)),
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AdmissionConflict', result.stderr)
        self.assert_code('MART_BUSY', marts.save_mart, self.owner, self.context, key='edit-active',
            mart_id=mart['id'], expected_revision=1, definition=mart['definition'])

    def test_mart_rejects_existing_job_scan_and_root_dataops(self):
        mart, _ = self.create()
        for kind in ('job', 'stage_scan', 'dataops'):
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                handle = admission.admit(db, warehouse_id='w', actor=self.owner.principal_id,
                    kind=kind, subject_id='existing-' + kind)
            with self.assertRaises(admission.AdmissionConflict):
                self.refresh(mart, 'blocked-' + kind)
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                admission.finish(db, handle, state='FAILED')
        self.assertEqual(self.w.posts, [])

    def test_normal_http_contract_and_strict_header(self):
        from types import SimpleNamespace
        import test_stage_routes as http_fixture
        import runtime_profile
        import workbench_server
        http = http_fixture.StageHTTPTests()
        http.f, http.c = SimpleNamespace(b=self.b), asdict(self.context)
        http.loopback, http.native = False, False
        source = self.w.existing()
        model = self.w.save(source=source, target=self.b.locator('http_target', schema='marts'))
        definition = dict(name='http', model_id=model['model_id'], version=model['version'],
            source_refs=[asdict(source)], target=model['target'])
        with patch.object(runtime_profile, 'active', return_value=True), patch.object(workbench_server.WorkbenchHandler, 'log_message'):
            status, response = http.request('/api/v1/marts', body=dict(context=http.c, expected_revision=0, definition=definition),
                headers=[('X-Idempotency-Key', 'http-create')])
            self.assertEqual(status, 200, response)
            mart = response['mart']
            self.assertEqual(self.w.posts, [])
            status, response = http.request('/api/v1/marts')
            self.assertEqual(status, 200, response)
            self.assertEqual(response['items'][0]['id'], mart['id'])
            path = '/api/v1/marts/' + mart['id']
            status, response = http.request(path, body=dict(context=http.c, expected_revision=1,
                definition=dict(definition, name='renamed')), headers=[('X-Idempotency-Key', 'http-update')])
            self.assertEqual(status, 200, response)
            self.assertEqual(response['mart']['revision'], 2)
            status, response = http.request(path + '/refreshes', body=dict(context=http.c, expected_revision=2), key='wrong-header')
            self.assertEqual(status, 400, response)
            status, response = http.request(path + '/refreshes', body=dict(context=http.c, expected_revision=2),
                headers=[('X-Idempotency-Key', 'http-refresh')])
            self.assertEqual(status, 202, response)
            self.assertEqual(response['run']['kind'], 'refresh')
            self.assertFalse(any(k.startswith('_') for k in response['run']))
            run_id = response['run']['id']
            status, response = http.request(path + '/runs/' + run_id)
            self.assertEqual(status, 200, response)
            self.assertTrue(response['run']['committed'])
            status, response = http.request(path + '/runs')
            self.assertEqual(status, 200, response)
            self.assertTrue(response['items'])
            status, response = http.request(path + '/checks', body=dict(context=http.c, expected_revision=3),
                headers=[('X-Idempotency-Key', 'http-check')])
            self.assertEqual(status, 202, response)
            self.assertEqual(response['run']['status'], 'queued')
            status, response = http.request(path + '/runs/' + response['run']['id'])
            self.assertEqual(status, 200, response)
            self.assertTrue(response['run']['result']['unchanged'])
            status, response = http.request(path + '/checks', body=dict(context=http.c, expected_revision=3, check_changes=True),
                headers=[('X-Idempotency-Key', 'bad-check')])
            self.assertEqual(status, 400, response)

    def test_actual_mart_client_default_native_bootstrap_session_and_published_model_save(self):
        from contextlib import redirect_stdout
        import io
        from types import SimpleNamespace
        from urllib.error import HTTPError
        from urllib.parse import parse_qs, urlsplit
        import dataops_client
        import marts_client
        import runtime_profile
        import test_stage_routes as http_fixture
        import warehouse_resident

        # The real client selects LOCAL; align only this owned unit fixture.
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL' WHERE id=?",
                (self.context.generation_id,))
        self.context = replace(self.context, warehouse_id='LOCAL')
        self.w.context = replace(self.w.context, warehouse_id='LOCAL')
        built, audited = self.w.audited()
        with control_store.connect() as db:
            immutable = db.execute('SELECT record FROM wap_model_versions WHERE database_id=? AND model_id=? AND version=1',
                (self.context.database_id, built['model_id'])).fetchone()[0]
        publication = wap.publish(self.owner, self.context, key='client-native-publish',
            build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(publication['state'], 'FINISHED')
        model = wap.get_model(self.owner, self.context, model_id=built['model_id'], version=1)
        self.assertEqual(model['current_target_ref'], publication['result']['publication']['target']['ref'])
        self.assertEqual(model['target_kind'], 'locator')
        definition = dict(name='client-native-new', model_id=model['model_id'], version=model['version'],
            source_refs=list(model['sources'].values()), target=model['current_target_ref'])
        root = self.b.fixture.root
        auth_file, definition_file, context_file = (root / name for name in
            ('mart-client-auth.json', 'mart-definition.json', 'mart-context.json'))
        for path, value in ((auth_file, dict(username='owner', password='correct', selected_role='admin')),
                (definition_file, definition), (context_file, asdict(self.context))):
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        profile = SimpleNamespace(client_auth_file=str(auth_file))
        args = ['save', '--database='+self.context.database_id, '--schema='+self.context.schema_id,
            '--definition='+str(definition_file), '--expected-revision=0', '--idempotency-key=client-native-create']
        context_args = [arg for arg in args if not arg.startswith(('--database=', '--schema='))]
        context_args.append('--context-file='+str(context_file))
        http = http_fixture.StageHTTPTests()
        http.f, http.c = SimpleNamespace(b=self.b), asdict(self.context)
        http.loopback, http.native = False, True
        calls, mode = [], 'normal'
        class Opener:
            def open(opener, req, **kwargs):
                url = urlsplit(req.full_url)
                headers = [(k, v) for k, v in req.header_items()
                    if mode != 'missing-auth' or k.lower() != 'authorization']
                status, reply = http.request(url.path + ('?' + url.query if url.query else ''),
                    body=json.loads(req.data) if req.data is not None else None, context=False,
                    method=req.get_method(), request_headers=headers)
                calls.append((url.scheme + '://' + url.netloc, url.path, req.get_method(), parse_qs(url.query), status))
                raw = json.dumps(reply).encode()
                if status >= 400:
                    raise HTTPError(req.full_url, status, 'unit refusal', {}, io.BytesIO(raw))
                response = io.BytesIO(raw)
                response.headers = {'Content-Length': str(len(raw))}
                return response

        count = len(self.w.posts)
        with control_store.connect() as db:
            admitted = db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0]
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(dataops_client, 'build_opener', return_value=Opener()), \
                patch.object(warehouse_resident, '_native_request', side_effect=AssertionError('native SQL fallthrough')) as native:
            reply = marts_client.main(args, profile=profile)
            self.assertEqual(reply['mart']['definition'], definition)
            self.assertEqual(reply['mart']['owner'], self.owner.principal_id)
            self.assertEqual(reply['mart']['context'], asdict(self.context))
            self.assertEqual([(path, method) for _, path, method, _, _ in calls], [
                ('/api/v1/databases', 'GET'),
                ('/api/v1/databases/' + self.context.database_id + '/schemas', 'GET'),
                ('/api/session', 'GET'), ('/api/v1/wap/models', 'GET'), ('/api/v1/marts', 'POST')])
            self.assertEqual({origin for origin, *_ in calls}, {'http://127.0.0.1:8080'})
            self.assertEqual(calls[3][3]['model_id'], [model['model_id']])
            self.assertEqual(calls[3][3]['version'], ['1'])
            self.assertEqual(json.loads(calls[3][3]['context'][0]), asdict(self.context))
            status, session = http.request('/api/session', context=False)
            self.assertEqual(status, 200)
            self.assertEqual(session, dict(username='owner', role='admin', maximum_role='admin',
                authenticated=True, data_mode='database', platform_admin=False))
            status, session = http.request('/api/session', context=False, identity='alice', role='bi_viewer')
            self.assertEqual(status, 200)
            self.assertEqual(session['role'], 'bi_viewer')
            self.assertEqual(session['maximum_role'], 'data_engineer')
            self.assertFalse(session['platform_admin'])
            for options, expected in (({'identity': None}, 401), ({'role': 'invalid'}, 400),
                    ({'headers': [('X-Shirokuma-Role', 'admin')]}, 400),
                    ({'request_headers': list(self.b.fixture.headers('owner').items())}, 400),
                    ({'request_headers': [('X-Shirokuma-Role', 'admin'),
                        *self.b.fixture.headers('owner', password='incorrect').items()]}, 401),
                    ({'identity': 'alice'}, 403)):
                status, error = http.request('/api/session', context=False, **options)
                self.assertEqual(status, expected)
                self.assertNotIn('username', error)
                self.assertNotIn('authenticated', error)
            status, error = http.request('/api/session?extra=1', context=False)
            self.assertEqual(status, 400)
            self.assertNotIn('username', error)
            status, error = http.request('/api/session', context=False, method='GET', body={})
            self.assertEqual(status, 400)
            self.assertNotIn('username', error)

            output = io.StringIO()
            mode = 'missing-auth'
            with redirect_stdout(output), self.assertRaises(marts_client.ClientError) as denied:
                marts_client.main(args, profile=profile)
            self.assertIsNone(denied.exception.reply)
            self.assertEqual(calls[-1][4], 401)
            mode = 'normal'
            auth_file.write_text(json.dumps(dict(username='alice', password='correct', selected_role='admin')))
            with redirect_stdout(output), self.assertRaisesRegex(ValueError, 'explicit unambiguous'):
                marts_client.main(args, profile=profile)
            with redirect_stdout(output), self.assertRaises(marts_client.ClientError) as denied:
                marts_client.main(context_args, profile=profile)
            self.assertIsNone(denied.exception.reply)
            self.assertEqual((calls[-1][1], calls[-1][4]), ('/api/session', 403))
            auth_file.write_text(json.dumps(dict(username='owner', password='correct', selected_role='admin')))
            self.downgrade(revoke=True)
            status, session = http.request('/api/session', context=False)
            self.assertEqual(status, 200)
            self.assertEqual(session['username'], self.owner.principal_id)
            self.assertFalse(session['platform_admin'])
            with redirect_stdout(output), self.assertRaisesRegex(ValueError, 'explicit unambiguous'):
                marts_client.main(args, profile=profile)
            with redirect_stdout(output), self.assertRaises(marts_client.ClientError) as denied:
                marts_client.main(context_args, profile=profile)
            self.assertIsNone(denied.exception.reply)
            self.assertEqual((calls[-1][1], calls[-1][4]), ('/api/v1/wap/models', 403))
            status, error = http.request('/api/v1/marts',
                body=dict(context=asdict(self.context), expected_revision=0, definition=definition),
                headers=[('X-Idempotency-Key', 'revoked-create')])
            self.assertEqual(status, 403)
            self.assertNotIn('mart', error)
            self.assertEqual(output.getvalue(), '')
            native.assert_not_called()
        self.assertEqual(len(self.w.posts), count)
        self.assertEqual(json.loads(definition_file.read_text()), definition)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], admitted)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_marts').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT record FROM wap_model_versions WHERE database_id=? AND model_id=? AND version=1',
                (self.context.database_id, model['model_id'])).fetchone()[0], immutable)

    def test_native_model_read_and_new_mart_chain_preserve_authority_without_sql(self):
        from types import SimpleNamespace
        from urllib.parse import urlencode
        import test_stage_routes as http_fixture
        import runtime_profile
        import query_service
        import warehouse_resident

        built, audited = self.w.audited()
        original = wap.get_model(self.owner, self.context, model_id=built['model_id'], version=1)
        definition = dict(name='before-publish', model_id=original['model_id'], version=1,
            source_refs=list(original['sources'].values()), target=original['target'])
        previous = marts.save_mart(self.owner, self.context, key='before-native-publish',
            expected_revision=0, definition=definition)
        receipt = wap.publish(self.owner, self.context, key='native-publish',
            build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(receipt['state'], 'FINISHED')
        current = receipt['result']['publication']['target']['ref']
        count = len(self.w.posts)
        http = http_fixture.StageHTTPTests()
        http.f, http.c = SimpleNamespace(b=self.b), asdict(self.context)
        http.loopback, http.native = False, True
        path = '/api/v1/wap/models'
        query = {'context': json.dumps(http.c), 'model_id': original['model_id'], 'version': '1'}
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(runtime_profile, 'current', return_value=SimpleNamespace(sql_endpoint_url='http://127.0.0.1:1234', mode='database')), \
                patch.object(query_service, '_transport', side_effect=AssertionError('SQL proxy entered')) as sql, \
                patch.object(warehouse_resident, '_native_request', wraps=warehouse_resident._native_request) as native:
            status, reply = http.request(path + '?' + urlencode(query), context=False)
            self.assertEqual(status, 200)
            model = reply['model']
            self.assertEqual(model, dict(original, current_target_ref=current))
            self.assertEqual(reply['context'], http.c)
            status, reply = http.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(reply['items'], [model])
            definition.update(name='native-new', target=model['current_target_ref'])
            status, reply = http.request('/api/v1/marts',
                body=dict(context=http.c, expected_revision=0, definition=definition),
                headers=[('X-Idempotency-Key', 'native-new')])
            self.assertEqual(status, 200)
            self.assertEqual(reply['mart']['definition'], definition)
            self.assertEqual(reply['mart']['owner'], self.owner.principal_id)
            status, reply = http.request('/api/v1/marts/' + previous['id'])
            self.assertEqual(status, 200)
            self.assertIsNone(reply['mart']['current_target_ref'])
            self.assertEqual(reply['mart']['definition']['target'], original['target'])
            status, reply = http.request('/api/v1/marts/' + previous['id'],
                body=dict(context=http.c, expected_revision=1, definition=dict(definition, name='native-update')),
                headers=[('X-Idempotency-Key', 'native-update')])
            self.assertEqual(status, 200)
            self.assertEqual(reply['mart']['definition']['target'], current)

            for options, expected in (({'identity': None}, 401), ({'role': 'invalid'}, 400),
                    ({'headers': [('X-Shirokuma-Role', 'admin')]}, 400),
                    ({'request_headers': list(self.b.fixture.headers('owner').items())}, 400),
                    ({'identity': 'alice'}, 403)):
                status, reply = http.request(path, **options)
                self.assertEqual(status, expected)
                self.assertNotIn('model', reply)
                self.assertNotIn('items', reply)
            for bad, expected in (({k: v for k, v in http.c.items() if k != 'generation_id'}, 400),
                    ({**http.c, 'grant_revision': http.c['grant_revision'] + 1}, 409),
                    ({**http.c, 'generation_id': 'stale'}, 409)):
                status, reply = http.request(path + '?' + urlencode({'context': json.dumps(bad)}), context=False)
                self.assertEqual(status, expected)
                self.assertNotIn('model', reply)
                self.assertNotIn('items', reply)
            native.assert_not_called()

            mutation = dict(context=http.c, model_id='not-mounted', sql=original['sql'],
                sources=original['sources'], target=original['target'], expected_version=None)
            for wrong_path, body, method in ((path, mutation, 'POST'), (path, None, 'DELETE'),
                    (path + '/', None, 'GET'), (path + '/suffix', None, 'GET'),
                    ('/api/v1/wap/build', dict(context=http.c, model_id=original['model_id'], version=1), 'POST'),
                    ('/api/v1/wap/audit', dict(context=http.c, build_id=built['id']), 'POST'),
                    ('/api/v1/wap/publish', dict(context=http.c, build_id=built['id'], audit_id=audited['id']), 'POST'),
                    ('/api/v1/wap/rollback', dict(context=http.c, publication_id=receipt['result']['publication']['id']), 'POST'),
                    ('/api/session', {}, 'POST'), ('/api/session', None, 'DELETE'),
                    ('/api/session/', None, 'GET'), ('/api/session/suffix', None, 'GET'),
                    ('/api/login', {}, 'POST')):
                status, reply = http.request(wrong_path, body=body, method=method,
                    headers=[('Idempotency-Key', 'not-mounted')])
                self.assertEqual(status, 404)
                self.assertNotIn('model', reply)
                self.assertNotIn('operation', reply)
                self.assertNotIn('username', reply)
            self.assertEqual(native.call_count, 13)
            newer = wap.save_model(self.owner, self.context, model_id=original['model_id'], sql=original['sql'],
                sources={k: dm.ResourceRef(**v) for k, v in original['sources'].items()},
                target=dm.ResourceRef(**current), expected_version=1)
            self.assertEqual(newer['version'], 2)
            status, reply = http.request(path + '?' + urlencode(query), context=False)
            self.assertEqual(status, 200)
            self.assertEqual(reply['model'], model)
            status, reply = http.request('/api/v1/marts',
                body=dict(context=http.c, expected_revision=0, definition=dict(definition, name='old-version')),
                headers=[('X-Idempotency-Key', 'old-version')])
            self.assertEqual(status, 409)
            self.assertEqual(reply['code'], 'MODEL_VERSION_CHANGED')
            self.downgrade(revoke=True)
            status, reply = http.request(path)
            self.assertEqual(status, 403)
            self.assertNotIn('model', reply)
            self.assertNotIn('items', reply)
            sql.assert_not_called()
        self.assertEqual(len(self.w.posts), count)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_marts').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wap_models').fetchone()[0], 1)

    def test_native_mart_routes_precede_sql_proxy_and_enforce_http_boundary(self):
        from types import SimpleNamespace
        import test_stage_routes as http_fixture
        import runtime_profile
        import query_service
        http = http_fixture.StageHTTPTests()
        http.f, http.c = SimpleNamespace(b=self.b), asdict(self.context)
        http.loopback, http.native = False, True
        mart, _ = self.create()
        body = dict(context=http.c, expected_revision=1)
        path = '/api/v1/marts/' + mart['id'] + '/checks'
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(query_service, '_transport', side_effect=AssertionError('SQL proxy entered')) as sql:
            status, listing = http.request('/api/v1/marts')
            self.assertEqual(status, 200, listing)
            self.assertEqual(listing['items'][0]['id'], mart['id'])
            self.assertEqual(http.request(path, body=body, key='wrong-header')[0], 400)
            self.assertEqual(http.request(path, body=body,
                headers=[('X-Idempotency-Key', 'a'), ('X-Idempotency-Key', 'b')])[0], 400)
            self.assertEqual(http.request('/api/v1/marts', identity=None)[0], 401)
            self.assertEqual(http.request(path, body=body,
                headers=[('X-Idempotency-Key', 'origin'), ('Origin', 'https://foreign.example')])[0], 403)
            status, accepted = http.request(path, body=body, headers=[('X-Idempotency-Key', 'native-check')])
            self.assertEqual(status, 202, accepted)
            self.assertEqual(accepted['run']['status'], 'queued')
            sql.assert_not_called()


class MigrationTests(unittest.TestCase):
    def test_schema15_actual_copy_preserves_all_tables_rows_and_blobs(self):
        # Build the actual schema15 using the existing bootstrap, stopping only
        # the new migration. Copy SQLite, then migrate the copy with normal code.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(root)):
                with control_store.connect() as db:
                    from test_control_store import drop_creation_schema
                    drop_creation_schema(db)
                    for table in ('db_mart_operations', 'db_mart_requests', 'db_mart_runs', 'db_marts'):
                        db.execute('DROP TABLE ' + table)
                    db.execute('PRAGMA user_version=15')
                    db.execute("INSERT INTO jobs VALUES ('kept','owner',?,'then','then')", (sqlite3.Binary(b'\x00\xffold'),))
                path = root / 'jobs.sqlite3'
                copy = root / 'copy.sqlite3'
                with sqlite3.connect(path) as original, sqlite3.connect(copy) as target:
                    original.backup(target)
                path.write_bytes(copy.read_bytes())
                with sqlite3.connect(path) as db:
                    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    before = {name: db.execute('SELECT * FROM "' + name + '"').fetchall() for name in names}
                    self.assertTrue({'jobs', 'warehouse_admissions', 'database_retirements',
                                     'database_retirement_requests'} <= set(names))
                    self.assertNotIn('db_marts', names)
                with control_store.connect() as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], control_store.VERSION)
                    for name, rows in before.items():
                        self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM "' + name + '"')], rows, name)
                    self.assertEqual(db.execute('SELECT definition FROM jobs').fetchone()[0], b'\x00\xffold')


if __name__ == '__main__':
    unittest.main()
