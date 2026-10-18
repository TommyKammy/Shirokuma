"""Opt-in Phase 18 integration through shared HTTP and resident TLS Iceberg.

Uses the existing isolated-server harness; all state, sources and synthetic
tables are disposable. No listener on the normal Workbench port is changed.
"""
import csv
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
import unittest
from unittest.mock import patch


_spec = importlib.util.spec_from_file_location(
    'stage_export_integration_harness', Path(__file__).with_name('test_runtime_stage_exports.py'))
_exports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_exports)
_harness = _exports._harness
_warehouse = _harness.warehouse


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for Phase 18 integration')
class RuntimeStageIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(_harness.RuntimeStagesTests.setUpClass.__func__)
    setUp = _harness.RuntimeStagesTests.setUp
    sql = _harness.RuntimeStagesTests.sql
    cleanup_tables = _harness.RuntimeStagesTests.cleanup_tables
    start_workbench = _harness.RuntimeStagesTests.start_workbench
    stop_workbench = staticmethod(_harness.RuntimeStagesTests.stop_workbench)
    http = _harness.RuntimeStagesTests.http
    request = _harness.RuntimeStagesTests.request
    wait_run = _harness.RuntimeStagesTests.wait_run
    stable_file = _harness.RuntimeStagesTests.stable_file
    stage_scan = _harness.RuntimeStagesTests.stage_scan

    def save_stage(self, name, directory, table, **options):
        definition = dict(
            name=name, directory=str(directory), table=table,
            mode='append', format='auto')
        definition.update(options)
        return self.request('/api/stages/save', definition)['stage']

    def rows(self, table=None):
        return self.sql('SELECT id, code FROM ' +
                        _warehouse.identifier(table or self.table) + ' ORDER BY id')['rows']

    def import_file(self, name, filename):
        selected = next(item for item in self.request(
            '/api/stages/files', {'name': name})['files'] if item['name'] == filename)
        return self.request('/api/stages/import', dict(
            name=name, file=filename, version=selected['version']), expected=202)['run']

    def export_payload(self, stage, filename):
        return dict(name=stage['name'], table=self.table, file=filename,
                    format=filename.rsplit('.', 1)[1], version=stage['updated_at'])

    def force_scheduled_scan(self, name):
        # Exercise the server's genuine scheduler and worker, while moving only
        # this isolated state's persisted due time instead of waiting a minute.
        before = {run['id'] for run in self.request('/api/jobs/runs')['runs']}
        due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.directory / 'state' / 'jobs.sqlite3') as connection:
            changed = connection.execute('UPDATE schedules SET next_run_at=? WHERE name=?',
                                         (due, 'stage:' + name)).rowcount
        self.assertEqual(changed, 1)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            runs = [run for run in self.request('/api/jobs/runs')['runs']
                    if run['id'] not in before and run['job_name'] == 'stage:' + name
                    and run['trigger'] == 'schedule']
            if runs:
                run = self.wait_run(runs[0]['id'])
                self.assertEqual(run['status'], 'success', run)
                self.assertEqual((run['actor'], run['role']), ('alice', 'admin'))
                return run
            time.sleep(.1)
        self.fail('Existing scheduler did not consume the isolated due stage')

    def ledger(self):
        with sqlite3.connect(self.directory / 'state' / 'jobs.sqlite3') as connection:
            return connection.execute('SELECT stage_name,content_key,status,last_run_id,position '
                                      'FROM stage_files ORDER BY stage_name,content_key').fetchall()

    def test_manual_schedule_export_roundtrip_restart_and_changed_content(self):
        source = self.directory / 'incoming'
        source.mkdir()
        first = self.stable_file(source, 'a.csv', 'id,code\n1,00123\n')
        second_table = self.table + '_roundtrip'
        for table in (self.table, second_table):
            self.sql('CREATE TABLE ' + _warehouse.identifier(table) + ' (id BIGINT, code VARCHAR)')
        child = self.start_workbench()
        saved = self.save_stage('integrated_input', source, self.table)
        manual = self.wait_run(self.import_file(saved['name'], 'a.csv')['id'])
        self.assertEqual(manual['status'], 'success', manual)
        first.rename(source / 'renamed.csv')
        self.stable_file(source, 'duplicate.csv', 'id,code\n1,00123\n')
        self.stable_file(source, 'b.csv', 'id,code\n2,00007\n')
        schedule_payload = dict(name=saved['name'], every_minutes=10, enabled=True,
                                version=saved['updated_at'])
        self.request('/api/stages/schedule', schedule_payload)
        scheduled = self.force_scheduled_scan(saved['name'])
        self.assertEqual(len(scheduled['definition']['steps']), 1)
        self.assertEqual(self.rows(), [[1, '00123'], [2, '00007']])
        files = self.request('/api/stages/files', {'name': saved['name']})['files']
        self.assertEqual({item['name']: item['ingestion_status'] for item in files},
                         {'renamed.csv': 'success', 'duplicate.csv': 'success', 'b.csv': 'success'})
        self.assertEqual({item['last_run_id'] for item in files if item['name'] != 'b.csv'}, {manual['id']})

        original = {}
        for suffix in ('jsonl', 'csv'):
            filename = 'roundtrip.' + suffix
            submitted = self.request('/api/stages/export', self.export_payload(saved, filename), expected=202)['run']
            run = self.wait_run(submitted['id'])
            self.assertEqual(run['status'], 'success', run)
            self.assertEqual(run['steps'][0]['summary']['row_count'], 2)
            path = source / 'exports' / filename
            original[filename] = path.read_bytes()
            if suffix == 'jsonl':
                actual = [json.loads(line) for line in path.read_text().splitlines()]
            else:
                with path.open(newline='') as stream:
                    actual = [dict(id=int(row['id']), code=row['code']) for row in csv.DictReader(stream)]
            self.assertEqual(sorted(actual, key=lambda row: row['id']),
                             [dict(id=1, code='00123'), dict(id=2, code='00007')])
            timestamp = time.time() - 40
            os.utime(path, (timestamp, timestamp))
        self.assertEqual(len(self.request('/api/stages/files', {'name': saved['name']})['files']), 3)
        empty = self.force_scheduled_scan(saved['name'])
        self.assertEqual([step['action'] for step in empty['definition']['steps']], ['stage_scan_empty'])
        self.assertEqual(self.rows(), [[1, '00123'], [2, '00007']])

        exported = self.save_stage('explicit_output_input', source / 'exports', second_table,
                                   mode='upsert', key='id')
        roundtrip = self.wait_run(self.import_file(exported['name'], 'roundtrip.jsonl')['id'])
        self.assertEqual(roundtrip['status'], 'success', roundtrip)
        # CSV and JSONL have different content identities. Upsert deliberately
        # consumes the other representation once, retaining the same rows.
        alternate = self.stage_scan(exported['name'])
        self.assertEqual(len(alternate['definition']['steps']), 1)
        output_scan = self.stage_scan(exported['name'])
        self.assertEqual([step['action'] for step in output_scan['definition']['steps']], ['stage_scan_empty'])
        self.assertEqual(self.rows(second_table), self.rows())

        for user, denied in [('bob', 404), ('emily', 403), ('vera', 403), (None, 401)]:
            for endpoint, payload in [('/api/stages/import', dict(name=saved['name'], file='b.csv', version=files[0]['version'])),
                                      ('/api/stages/export', self.export_payload(saved, 'denied.csv')),
                                      ('/api/stages/schedule', schedule_payload),
                                      ('/api/stages/scan', dict(name=saved['name'], version=saved['updated_at']))]:
                self.request(endpoint, payload, user=user, expected=denied)
        audit = self.request('/api/stages', user='bob')['stages']
        self.assertTrue(all(not item['can_manage'] for item in audit))
        runs = self.request('/api/jobs/runs')['runs']
        self.assertEqual({run['id'] for run in self.request('/api/jobs/runs', user='bob')['runs']},
                         {run['id'] for run in runs})
        stages = self.request('/api/stages')['stages']
        ledger = self.ledger()
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assertEqual(self.request('/api/stages')['stages'], stages)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.request('/api/jobs/runs')['runs'], runs)
        for filename, content in original.items():
            self.assertEqual((source / 'exports' / filename).read_bytes(), content)
        empty = self.force_scheduled_scan(saved['name'])
        self.assertEqual([step['action'] for step in empty['definition']['steps']], ['stage_scan_empty'])
        self.stable_file(source, 'b.csv', 'id,code\n3,00042\n')
        changed = self.force_scheduled_scan(saved['name'])
        self.assertEqual(len(changed['definition']['steps']), 1)
        self.assertEqual(self.rows(), [[1, '00123'], [2, '00007'], [3, '00042']])
        self.assertEqual(self.rows(second_table), [[1, '00123'], [2, '00007']])
        self.assertEqual(len(self.ledger()), 5)
        self.stop_workbench(child)

    def start_held_import(self):
        # Inject a process pause after real Iceberg publication, using the normal
        # harness startup/poll/cleanup. Production has no fault-injection knob.
        server = Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench_server.py'
        self.marker = self.directory / 'input-published.marker'
        launcher = (
            'import sys,runpy,time; from pathlib import Path; '
            'sys.path.insert(0,' + repr(str(server.parent)) + '); '
            'import lake_ingest; original=lake_ingest.ingest\n'
            'def publish_and_hold(*args, **kwargs):\n'
            '    result=original(*args, **kwargs)\n'
            '    Path(' + repr(str(self.marker)) + ').write_text("published")\n'
            '    time.sleep(300)\n'
            '    return result\n'
            'lake_ingest.ingest=publish_and_hold\n'
            'sys.argv=[' + repr(str(server)) + ',"--port","0"]; '
            'runpy.run_path(' + repr(str(server)) + ',run_name="__main__")')
        original_popen = _harness.subprocess.Popen

        def spawn(command, **kwargs):
            return original_popen([command[0], '-c', launcher], **kwargs)

        with patch.object(_harness.subprocess, 'Popen', side_effect=spawn):
            return self.start_workbench()

    def kill_after_publication(self, child):
        deadline = time.monotonic() + 45
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        self.assertTrue(self.marker.exists(), 'Real publication did not complete before crash')
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)

    def test_orphan_export_and_input_claim_recovery_remain_separate(self):
        source = self.directory / 'recovery'
        source.mkdir()
        self.stable_file(source, 'a.csv', 'id,code\n1,00123\n')
        self.sql('CREATE TABLE ' + _warehouse.identifier(self.table) + ' (id BIGINT, code VARCHAR)')
        child = self.start_workbench()
        stage = self.save_stage('recovery_integration', source, self.table)
        successful = self.wait_run(self.import_file(stage['name'], 'a.csv')['id'])
        self.assertEqual(successful['status'], 'success', successful)
        initial_ledger = self.ledger()
        self.stop_workbench(child)
        child = _exports.RuntimeStageExportsTests.start_workbench(self, hold_after_export=True)
        exported = self.request('/api/stages/export', self.export_payload(stage, 'orphan.jsonl'), expected=202)['run']
        selected = self.request('/api/stages/files', {'name': stage['name']})['files'][0]
        self.request('/api/stages/import', dict(name=stage['name'], file='a.csv',
                     version=selected['version']), expected=409)
        self.request('/api/stages/export', self.export_payload(stage, 'busy.csv'), expected=409)
        self.kill_after_publication(child)
        output = source / 'exports' / 'orphan.jsonl'
        original = output.read_bytes()
        child = self.start_workbench()
        self.assertEqual(self.request('/api/jobs/detail', {'run_id': exported['id']})['run']['status'], 'unknown')
        self.assertEqual(self.ledger(), initial_ledger)
        self.assertEqual(self.rows(), [[1, '00123']])
        self.request('/api/stages/export', self.export_payload(stage, 'orphan.jsonl'), expected=400)
        self.stop_workbench(child)
        self.stable_file(source, 'b.csv', 'id,code\n2,00007\n')
        child = self.start_held_import()
        imported = self.import_file(stage['name'], 'b.csv')
        self.kill_after_publication(child)
        self.assertEqual(self.rows(), [[1, '00123'], [2, '00007']])
        child = self.start_workbench()
        recovered = self.request('/api/jobs/detail', {'run_id': imported['id']})['run']
        self.assertEqual(recovered['status'], 'unknown', recovered)
        files = self.request('/api/stages/files', {'name': stage['name']})['files']
        self.assertEqual({item['name']: item['ingestion_status'] for item in files},
                         {'a.csv': 'success', 'b.csv': 'unknown'})
        self.assertEqual({row[2] for row in self.ledger()}, {'success', 'unknown'})
        selected = next(item for item in files if item['name'] == 'b.csv')
        self.request('/api/stages/import', dict(name=stage['name'], file='b.csv',
                     version=selected['version']), expected=409)
        scan = self.stage_scan(stage['name'])
        self.assertEqual([step['action'] for step in scan['definition']['steps']], ['stage_scan_empty'])
        self.assertEqual(self.rows(), [[1, '00123'], [2, '00007']])
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(len(self.request('/api/jobs/runs')['runs']), 4)
        self.assertEqual(self.request('/api/jobs/detail', {'run_id': exported['id']})['run']['status'], 'unknown')
        self.stop_workbench(child)


if __name__ == '__main__':
    unittest.main()
