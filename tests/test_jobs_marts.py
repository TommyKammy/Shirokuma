"""Registered mart admission, WAP publication and shared job ledger boundaries."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import bears_resident as b
import jobs_resident as jobs
import marts_resident as marts


class MartsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for manager in (patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root),
                SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''),
                patch.object(b, 'WAP_REGISTRY', self.root / 'wap_models.json'),
                patch.object(jobs.wr, 'telemetry', return_value='skipped')):
            manager.start()
            self.addCleanup(manager.stop)

    def save(self, **changes):
        return marts.save_target(dict(name='orders', sql='SELECT 1 AS id', **changes), 'alice', 'admin')

    def done(self, run_id):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            run = jobs.get_run(run_id, 'alice', 'admin')
            if run['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run_id[:8]:
                        thread.join(2)
                return run
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_save_is_offline_and_validates_sql_quality_and_revision(self):
        with patch.object(b.wr, 'trino') as trino:
            target = self.save(min_rows=1, not_null=['id'])
            self.assertEqual(target['version'], 1)
            self.assertEqual(target['table'], 'iceberg.marts.orders')
            self.assertIsNone(target['last_run'])
            self.assertIsNone(target['last_success_at'])
            trino.assert_not_called()
        for changes in ({'sql': 'DELETE FROM iceberg.marts.t'}, {'sql': 'SELECT 1; SELECT 2'},
                        {'min_rows': -1}, {'min_rows': 0}, {'min_rows': True}, {'not_null': ['COUNT(*)']},
                        {'not_null': 'id'}, {'name': 'marts.orders'}):
            payload = dict(name='other', sql='SELECT 1', **{})
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                marts.save_target(payload, 'alice', 'admin')
        for version in (None, True, 0, 2):
            with self.subTest(version=version), self.assertRaises(jobs.JobConflict):
                self.save(version=version)
        self.assertEqual(self.save(version=1)['version'], 2)
        self.assertEqual(json.loads(b.WAP_REGISTRY.read_text())['orders']['owner'], 'alice')

    def test_admin_owner_and_legacy_boundaries(self):
        self.save()
        for method, args in ((marts.list_targets, ()), (marts.save_target, ({'name': 'x', 'sql': 'SELECT 1'},)),
                             (marts.submit_refresh, ('orders', 1))):
            with self.assertRaises(PermissionError):
                method(*args, 'alice', 'data_engineer')
        self.assertFalse(marts.list_targets('bob', 'admin')[0]['can_manage'])
        with self.assertRaises(PermissionError):
            marts.save_target(dict(name='orders', sql='SELECT 2', version=1), 'bob', 'admin')
        with self.assertRaises(PermissionError):
            marts.submit_refresh('orders', 1, 'bob', 'admin')
        b.save_wap_entry('legacy', sql='SELECT 3')
        self.assertEqual(len(marts.list_targets('alice', 'admin')), 1)
        with self.assertRaises(jobs.JobConflict):
            marts.save_target(dict(name='LEGACY', sql='SELECT 4'), 'alice', 'admin')
        with self.assertRaises(PermissionError):
            b.save_wap_entry('orders', sql='SELECT 99')
        with self.assertRaises(PermissionError):
            b.save_wap_entry('ORDERS', sql='SELECT 99')
        for args in (SimpleNamespace(refresh='orders'), SimpleNamespace(publish='ORDERS'),
                     SimpleNamespace(stage_table='orders', sql='SELECT 99')):
            with patch.object(b.wr, 'trino') as trino, self.assertRaises(PermissionError):
                b.data(args)
            trino.assert_not_called()
        self.assertEqual(b.load_wap_registry()['orders']['sql'], 'SELECT 1 AS id')

    def test_admission_captures_snapshot_and_excludes_jobs_and_edits(self):
        self.save(min_rows=5, not_null=['id'])
        started, release = threading.Event(), threading.Event()
        captured = []
        def execute(step):
            captured.append(step)
            started.set()
            if not release.wait(5):
                raise ValueError('test timeout')
            return dict(valid=True, table='iceberg.marts.orders')
        with patch.object(jobs, '_execute_step', side_effect=execute), patch.object(jobs.wr, 'resolve_role'):
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(jobs.JobConflict):
                    self.save(version=1)
                with self.assertRaises(jobs.JobConflict):
                    marts.submit_refresh('orders', 1, 'alice', 'admin')
                with self.assertRaises(jobs.JobConflict):
                    jobs.submit_run('any', {}, 'alice', 'admin')
            finally:
                release.set()
            complete = self.done(run['id'])
        self.assertEqual(complete['status'], 'success')
        self.assertEqual(complete['definition']['steps'][0]['min_rows'], 5)
        self.assertEqual(captured[0]['not_null'], ['id'])
        self.save(version=1)
        self.assertEqual(jobs.get_run(run['id'], 'alice', 'admin')['definition']['steps'][0]['version'], 1)
        listing = marts.list_targets('alice', 'admin')[0]
        self.assertEqual(listing['last_run']['id'], run['id'])
        self.assertEqual(listing['last_success_at'], complete['finished_at'])
        with self.assertRaises(jobs.JobConflict):
            marts.submit_refresh('orders', 1, 'alice', 'admin')

    def test_restart_recovers_admitted_run_as_unknown_without_replay(self):
        self.save()
        def stopped(run_id, steps, actor, role, lock, *args):
            lock.close()
        with patch.object(jobs, '_start_worker', side_effect=stopped), patch.object(b, 'data') as execute:
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            recovered = marts.list_targets('alice', 'admin')[0]['last_run']
            self.assertEqual(recovered['id'], run['id'])
            self.assertEqual(recovered['status'], 'unknown')
            self.assertIn('no automatic retry', recovered['error'])
            execute.assert_not_called()

    def test_private_action_cannot_be_registered_as_public_job(self):
        with self.assertRaises(ValueError):
            jobs.save_job(dict(name='bypass', steps=[dict(action='mart_refresh', model='orders')]),
                          'alice', 'admin')

    def test_successful_publication_with_failed_ledger_write_is_unknown(self):
        self.save()
        original = jobs.json.dumps
        failed = []
        def serialize(value, *args, **kwargs):
            if isinstance(value, dict) and value.get('valid') is True and not failed:
                failed.append(True)
                raise OSError('ledger write failed')
            return original(value, *args, **kwargs)
        with patch.object(jobs.wr, 'resolve_role'), \
             patch.object(jobs, '_execute_step', return_value=dict(valid=True, table='iceberg.marts.orders')), \
             patch.object(jobs.json, 'dumps', side_effect=serialize):
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            complete = self.done(run['id'])
        self.assertEqual(complete['status'], 'unknown')
        self.assertEqual(complete['steps'][0]['status'], 'unknown')

    def test_quality_parameters_flow_to_wap_and_failed_quality_keeps_target(self):
        self.save(min_rows=3, not_null=['id'])
        statements = []
        def sql(url, statement):
            statements.append(statement)
            return {'rows': []}
        with patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
             patch.object(jobs.wr, 'resolve_role'), patch.object(b, 'sql', side_effect=sql), \
             patch.object(b, 'latest_snapshot', return_value=7), \
             patch.object(b, 'audit_table', return_value=dict(snapshot_id=7, row_count=2, null_counts={'id': 0})):
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            complete = self.done(run['id'])
        self.assertEqual(complete['status'], 'failed')
        self.assertIn('>= 3', complete['error'])
        self.assertTrue(any('CREATE OR REPLACE TABLE "iceberg"."sandbox"."orders"' in s for s in statements))
        self.assertFalse(any('CREATE OR REPLACE TABLE "iceberg"."marts"' in s for s in statements))
        self.assertEqual(b.load_wap_registry()['orders']['version'], 1)

    def test_success_preserves_registered_definition_and_late_ledger_failure_is_unknown(self):
        self.save(min_rows=1)
        statements = []
        def sql(url, statement):
            statements.append(statement)
            return {'rows': []}
        for fail_metadata in (False, True):
            with self.subTest(fail_metadata=fail_metadata), \
                 patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
                 patch.object(jobs.wr, 'resolve_role'), patch.object(b, 'sql', side_effect=sql), \
                 patch.object(b, 'latest_snapshot', return_value=7), \
                 patch.object(b, 'audit_table', side_effect=[dict(snapshot_id=7,row_count=1,null_counts={}),
                                                           dict(snapshot_id=8)]), \
                 patch.object(b.wap, 'persist_json', side_effect=OSError('disk full') if fail_metadata else None) as persist:
                if not fail_metadata:
                    persist.side_effect = lambda path, value: path.write_text(json.dumps(value))
                run = marts.submit_refresh('orders', 1, 'alice', 'admin')
                complete = self.done(run['id'])
            self.assertEqual(complete['status'], 'unknown' if fail_metadata else 'success')
            entry = b.load_wap_registry()['orders']
            self.assertEqual(entry['owner'], 'alice')
            self.assertEqual(entry['version'], 1)
            self.assertEqual(entry['min_rows'], 1)
            self.assertEqual(entry['sql'], 'SELECT 1 AS id')
        self.assertTrue(any('FOR VERSION AS OF 7' in s for s in statements))

    def test_shared_identity_revocation_before_work(self):
        self.save()
        account = self.root / 'accounts.json'
        account.write_text(json.dumps({'alice': {'role': 'admin'}}))
        def delayed(run_id, steps, actor, role, lock, shared, name):
            account.write_text('{}')
            jobs._worker(run_id, steps, actor, role, lock, shared, name)
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(account)), \
             patch.object(jobs, '_start_worker', side_effect=delayed), patch.object(b, 'data') as execute:
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            self.assertEqual(jobs.get_run(run['id'], 'alice', 'admin')['status'], 'failed')
            execute.assert_not_called()
