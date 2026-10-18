"""Opt-in registered Marts acceptance through isolated shared HTTP and real TLS/Iceberg."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import time
import unittest
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    'marts_runtime_harness', Path(__file__).with_name('test_runtime_stages.py'))
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
_warehouse = _harness.warehouse


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for registered Marts acceptance')
class RuntimeMartsTests(unittest.TestCase):
    # Reuse only methods: importing the TestCase itself would duplicate discovery.
    setUpClass = classmethod(_harness.RuntimeStagesTests.setUpClass.__func__)
    setUp = _harness.RuntimeStagesTests.setUp
    sql = _harness.RuntimeStagesTests.sql
    start_workbench = _harness.RuntimeStagesTests.start_workbench
    stop_workbench = staticmethod(_harness.RuntimeStagesTests.stop_workbench)
    http = _harness.RuntimeStagesTests.http
    request = _harness.RuntimeStagesTests.request

    def cleanup_tables(self):
        for schema in ('raw', 'sandbox', 'marts'):
            rows = self.sql('SELECT table_name FROM iceberg.information_schema.tables '
                'WHERE table_schema = ' + _warehouse.literal(schema) + ' AND table_name LIKE ' +
                _warehouse.literal(self.prefix + '%'))['rows']
            for row in rows:
                if row[0].startswith(self.prefix):
                    self.sql('DROP TABLE IF EXISTS ' + _warehouse.identifier('iceberg.' + schema + '.' + row[0]))

    def wait_run(self, run_id):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            run = self.request('/api/jobs/detail', {'run_id': run_id})['run']
            if run['status'] not in {'queued', 'running'}:
                return run
            time.sleep(.2)
        self.fail('Registered mart refresh did not reach a terminal state')

    def assert_no_replay(self, history):
        # Cross the scheduler startup and normal ten-second poll.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self.assertEqual(self.request('/api/jobs/runs')['runs'], history)
            time.sleep(.5)

    def mart(self, name):
        return next(item for item in self.request('/api/marts')['marts'] if item['name'] == name)

    def snapshot(self, table):
        # Observe the actual Iceberg main reference rather than mocked publication.
        return self.sql('SELECT snapshot_id FROM ' + _warehouse.identifier(table + '$refs') +
                        " WHERE name = 'main'")['rows'][0][0]

    def baseline(self, name):
        return json.loads((self.directory / 'state/wap_models.json').read_text())[name].get('watch_baseline')

    def wait_next_run(self, name, previous, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            runs = [run for run in self.request('/api/jobs/runs')['runs']
                    if run['job_name'] == 'mart:' + name and run['id'] not in previous]
            if runs:
                self.assertEqual(len(runs), 1, runs)
                return self.wait_run(runs[0]['id'])
            time.sleep(.25)
        self.fail('Mart scheduler did not admit the next check')

    def check_after_restart(self, child, name):
        previous = {run['id'] for run in self.request('/api/jobs/runs')['runs']}
        self.stop_workbench(child)
        # Model an upcoming interval occurrence in the disposable ledger while
        # stopped. Real startup admission and the real TLS worker execute it.
        due = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        with sqlite3.connect(self.directory / 'state/jobs.sqlite3') as connection:
            changed = connection.execute('UPDATE schedules SET next_run_at=? WHERE name=? AND enabled=1',
                                         (due, 'mart:' + name))
            self.assertEqual(changed.rowcount, 1)
        child = self.start_workbench()
        return child, self.wait_next_run(name, previous)

    def test_scheduled_mart_real_snapshots_quality_repair_restart_and_restore(self):
        self.sql('CREATE TABLE ' + _warehouse.identifier(self.table) + ' (id BIGINT, amount BIGINT)')
        self.sql('INSERT INTO ' + _warehouse.identifier(self.table) + ' VALUES (1, 20), (2, 30)')
        name, target = self.prefix + '_auto', 'iceberg.marts.' + self.prefix + '_auto'
        definition = dict(name=name, sql='SELECT id, amount FROM ' + self.table,
                          min_rows=1, not_null=['id'], watch_tables=[self.table])
        child = self.start_workbench()
        saved = self.request('/api/marts/save', definition)['mart']
        self.assertEqual(self.request('/api/jobs/runs')['runs'], [])
        self.assertEqual(self.sql("SELECT table_name FROM iceberg.information_schema.tables WHERE table_schema = 'marts' AND table_name = " + _warehouse.literal(name))['rows'], [])
        self.assertIsNone(self.baseline(name))
        schedule = dict(name=name, version=saved['version'], kind='interval', every_minutes=60, enabled=True)
        for user, status in [(None, 401), ('bob', 403), ('emily', 403), ('vera', 403)]:
            self.request('/api/marts/schedule', schedule, user=user, expected=status)
        self.request('/api/marts/schedule', {**schedule, 'version': 999999}, expected=409)
        self.request('/api/marts/schedule', {**schedule, 'owner': 'alice'}, expected=400)

        # A genuine upcoming minute exercises wall-clock scheduling without a
        # clock hook or ledger edit. A restart must preserve this future slot.
        due = (datetime.now(timezone.utc) + timedelta(seconds=95)).replace(second=0, microsecond=0)
        once = self.request('/api/marts/schedule', dict(name=name, version=saved['version'], enabled=True,
            kind='once', date=due.date().isoformat(), time=due.strftime('%H:%M'), timezone='UTC'))['schedule']
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assertEqual(self.mart(name)['schedule'], once)
        first = self.wait_next_run(name, set(), max(0, (due - datetime.now(timezone.utc)).total_seconds()) + 120)
        self.assertEqual((first['status'], first['actor'], first['role'], first['trigger']), ('success', 'alice', 'admin', 'schedule'), first)
        self.assertEqual(first['steps'][0]['summary']['result'], 'refreshed')
        self.assertFalse(self.mart(name)['schedule']['enabled'])
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], [[1, 20], [2, 30]])
        baseline, snapshot, published = self.baseline(name), self.snapshot(target), self.mart(name)['last_success_at']
        self.assertEqual(baseline, dict(version=saved['version'], snapshots={self.table: self.snapshot(self.table)}))
        self.request('/api/marts/schedule', schedule)
        child, unchanged = self.check_after_restart(child, name)
        self.assertEqual(unchanged['status'], 'success', unchanged)
        self.assertEqual(unchanged['steps'][0]['summary']['result'], 'unchanged')
        self.assertEqual((self.snapshot(target), self.mart(name)['last_success_at'], self.baseline(name)), (snapshot, published, baseline))

        self.sql('UPDATE ' + self.table + ' SET amount = 45 WHERE id = 1')
        self.sql('INSERT INTO ' + self.table + ' VALUES (3, 60)')
        child, changed = self.check_after_restart(child, name)
        self.assertEqual(changed['status'], 'success', changed)
        self.assertEqual(changed['steps'][0]['summary']['result'], 'refreshed')
        expected_rows = [[1, 45], [2, 30], [3, 60]]
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], expected_rows)
        self.assertNotEqual(self.snapshot(target), snapshot)
        baseline, snapshot, published = self.baseline(name), self.snapshot(target), self.mart(name)['last_success_at']
        self.sql('DELETE FROM ' + self.table)
        child, failed = self.check_after_restart(child, name)
        self.assertEqual(failed['status'], 'failed', failed)
        self.assertFalse(self.mart(name)['schedule']['enabled'])
        self.assertEqual((self.snapshot(target), self.mart(name)['last_success_at'], self.baseline(name)), (snapshot, published, baseline))
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], expected_rows)
        self.sql('INSERT INTO ' + self.table + ' VALUES (4, 70), (5, 80), (6, 90)')
        repaired = self.wait_run(self.request('/api/marts/run', dict(name=name, version=saved['version']), expected=202)['run']['id'])
        self.assertEqual(repaired['status'], 'success', repaired)
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], [[4, 70], [5, 80], [6, 90]])
        self.request('/api/marts/schedule', schedule)
        persisted, baseline = self.mart(name), self.baseline(name)
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assertEqual(self.mart(name), persisted)
        self.assertEqual(self.baseline(name), baseline)

        # Model interruption of an admitted worker in only this stopped ledger.
        # Recovery must expose unknown, pause, and prevent automatic replay.
        self.stop_workbench(child)
        with sqlite3.connect(self.directory / 'state/jobs.sqlite3') as connection:
            connection.execute("UPDATE runs SET status='running',finished_at=NULL WHERE id=?", (repaired['id'],))
            connection.execute("UPDATE steps SET status='running',finished_at=NULL WHERE run_id=?", (repaired['id'],))
        child = self.start_workbench()
        self.assertTrue(self.mart(name)['requires_manual_refresh'])
        self.assertFalse(self.mart(name)['schedule']['enabled'])
        self.request('/api/marts/schedule', schedule, expected=400)
        history = self.request('/api/jobs/runs')['runs']
        self.assert_no_replay(history)
        repaired = self.wait_run(self.request('/api/marts/run', dict(name=name, version=saved['version']), expected=202)['run']['id'])
        self.assertEqual(repaired['status'], 'success', repaired)
        self.assertFalse(self.mart(name)['requires_manual_refresh'])
        self.request('/api/marts/schedule', schedule)
        baseline = self.baseline(name)
        import disaster_recovery as dr
        archive = self.directory / 'scheduled-marts-backup.tar.gz'
        with patch.object(dr, '_catalog', return_value=({}, [])), patch.object(dr, 'MODELS_DIR', self.directory / 'no-models'):
            dr.backup(archive, include_tables=False)
        self.stop_workbench(child)
        recovered = self.directory / 'scheduled-recovered'
        with patch.object(dr, 'MODELS_DIR', recovered / 'models'):
            dr.restore(archive, target_state_dir=recovered)
        self.start_workbench(recovered)
        restored = self.mart(name)
        self.assertFalse(restored['schedule']['enabled'])
        self.assertIsNone(restored['schedule']['next_run_at'])
        self.assertEqual(json.loads((recovered / 'wap_models.json').read_text())[name]['watch_baseline'], baseline)
        history = self.request('/api/jobs/runs')['runs']
        self.assert_no_replay(history)
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], [[4, 70], [5, 80], [6, 90]])

    def test_registered_mart_full_refresh_quality_restart_and_restore(self):
        self.sql('CREATE TABLE ' + _warehouse.identifier(self.table) + ' (id BIGINT, amount BIGINT)')
        self.sql('INSERT INTO ' + _warehouse.identifier(self.table) + ' VALUES (1, 20), (2, 30)')
        name = self.prefix + '_mart'
        target = 'iceberg.marts.' + name
        definition = dict(name=name, sql='SELECT id, amount FROM ' + self.table,
                          min_rows=1, not_null=['id'])
        child = self.start_workbench()
        self.request('/api/marts', user=None, expected=401)
        for user in ('emily', 'vera'):
            self.request('/api/marts', user=user, expected=403)
            self.request('/api/marts/save', definition, user=user, expected=403)
            self.request('/api/marts/run', dict(name=name, version=1), user=user, expected=403)
        saved = self.request('/api/marts/save', definition)['mart']
        self.assertEqual((saved['owner'], saved['table'], saved['last_run']), ('alice', target, None))
        self.assertEqual(self.request('/api/jobs/runs')['runs'], [])
        self.assertEqual(self.sql("SELECT table_name FROM iceberg.information_schema.tables WHERE table_schema IN ('marts', 'sandbox') AND table_name = " + _warehouse.literal(name))['rows'], [])
        registry = json.loads((self.directory / 'state/wap_models.json').read_text())
        self.assertEqual(registry[name]['sql'], definition['sql'])
        other = self.request('/api/marts', user='bob')['marts'][0]
        self.assertFalse(other['can_manage'])
        for path, payload in [('/api/marts/save', {**definition, 'version': saved['version']}),
                              ('/api/marts/run', dict(name=name, version=saved['version']))]:
            self.request(path, payload, user='bob', expected=403)
        self.request('/api/marts/run', dict(name=name, version=999999), expected=409)
        admitted = self.request('/api/marts/run', dict(name=name, version=saved['version']), expected=202)['run']
        first = self.wait_run(admitted['id'])
        self.assertEqual((first['status'], first['actor'], first['role']), ('success', 'alice', 'admin'), first)
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], [[1, 20], [2, 30]])
        self.assertEqual(self.request('/api/marts')['marts'][0]['last_run']['id'], first['id'])

        # Source changes replace the complete published result; no incremental append.
        self.sql('UPDATE ' + self.table + ' SET amount = 45 WHERE id = 1')
        self.sql('INSERT INTO ' + self.table + ' VALUES (3, 60)')
        second = self.wait_run(self.request('/api/marts/run', dict(name=name, version=saved['version']), expected=202)['run']['id'])
        self.assertEqual(second['status'], 'success', second)
        expected_rows = [[1, 45], [2, 30], [3, 60]]
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], expected_rows)

        edited = self.request('/api/marts/save', {**definition, 'version': saved['version'], 'min_rows': 10})['mart']
        self.assertNotEqual(edited['version'], saved['version'])
        self.request('/api/marts/save', {**definition, 'version': saved['version']}, expected=409)
        self.request('/api/marts/run', dict(name=name, version=saved['version']), expected=409)
        failed = self.wait_run(self.request('/api/marts/run', dict(name=name, version=edited['version']), expected=202)['run']['id'])
        self.assertEqual(failed['status'], 'failed', failed)
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], expected_rows)
        persisted = self.request('/api/marts')['marts']
        history = self.request('/api/jobs/runs')['runs']
        self.assertEqual(len(history), 3)
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assertEqual(self.request('/api/marts')['marts'], persisted)
        self.assert_no_replay(history)

        import disaster_recovery as dr
        archive = self.directory / 'marts-backup.tar.gz'
        with patch.object(dr, '_catalog', return_value=({}, [])), patch.object(dr, 'MODELS_DIR', self.directory / 'no-models'):
            dr.backup(archive, include_tables=False)
        self.stop_workbench(child)
        recovered = self.directory / 'recovered'
        with patch.object(dr, 'MODELS_DIR', recovered / 'models'):
            dr.restore(archive, target_state_dir=recovered)
        self.start_workbench(recovered)
        self.assertEqual(self.request('/api/marts')['marts'], persisted)
        self.assertEqual(json.loads((recovered / 'wap_models.json').read_text())[name]['sql'], definition['sql'])
        self.assert_no_replay(history)
        self.assertEqual(self.sql('SELECT id, amount FROM ' + target + ' ORDER BY id')['rows'], expected_rows)


if __name__ == '__main__':
    unittest.main()
