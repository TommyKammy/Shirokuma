"""Opt-in registered Marts acceptance through isolated shared HTTP and real TLS/Iceberg."""
import importlib.util
import json
import os
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
