"""Opt-in Stage exports against an isolated Workbench and resident TLS Trino.

Run with SHIROKUMA_RUNTIME_TESTS=1 and the backend credential/CA variables.
Only uniquely named disposable Iceberg tables and temporary local files are used.
"""
import csv
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
import uuid

# Reuse the runtime harness without inheriting its acceptance test methods or
# exposing a second TestCase class to unittest discovery.
_spec = importlib.util.spec_from_file_location(
    'stage_runtime_harness', Path(__file__).with_name('test_runtime_stages.py'))
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for dedicated export acceptance')
class RuntimeStageExportsTests(unittest.TestCase):
    setUpClass = classmethod(_harness.RuntimeStagesTests.setUpClass.__func__)
    setUp = _harness.RuntimeStagesTests.setUp
    sql = _harness.RuntimeStagesTests.sql
    cleanup_tables = _harness.RuntimeStagesTests.cleanup_tables
    stop_workbench = staticmethod(_harness.RuntimeStagesTests.stop_workbench)
    http = _harness.RuntimeStagesTests.http
    request = _harness.RuntimeStagesTests.request
    wait_run = _harness.RuntimeStagesTests.wait_run

    def start_workbench(self, hold_after_export=False):
        if not hold_after_export:
            return _harness.RuntimeStagesTests.start_workbench(self)
        # Hold only after the real export has been durably published. Killing
        # this isolated child exercises actual jobs recovery without mocking SQL.
        server = Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench_server.py'
        self.marker = self.directory / 'published.marker'
        launcher = (
            'import sys,runpy,time; from pathlib import Path; '
            'sys.path.insert(0,' + repr(str(server.parent)) + '); '
            'import stage_exports; original=stage_exports._execute_export\n'
            'def publish_and_hold(step):\n'
            '    result=original(step)\n'
            '    Path(' + repr(str(self.marker)) + ').write_text("published")\n'
            '    time.sleep(300)\n'
            '    return result\n'
            'stage_exports._execute_export=publish_and_hold\n'
            'sys.argv=[' + repr(str(server)) + ',"--port","0"]; '
            'runpy.run_path(' + repr(str(server)) + ',run_name="__main__")')
        log_path = self.directory / ('held-' + uuid.uuid4().hex + '.log')
        environment = {**os.environ, 'SHIROKUMA_AUTH_MODE': 'shared',
                       'SHIROKUMA_AUTH_FILE': str(self.accounts),
                       'SHIROKUMA_STATE_DIR': str(self.directory / 'state')}
        with log_path.open('w') as log:
            child = subprocess.Popen([sys.executable, '-c', launcher], env=environment,
                                     stdout=log, stderr=log, start_new_session=True)
        self.addCleanup(self.stop_workbench, child)
        import re
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), 'Isolated export Workbench exited early')
            match = re.search(r'Shirokuma Workbench: (http://127\.0\.0\.1:\d+)', log_path.read_text())
            if match:
                self.http_url = match.group(1)
                try:
                    if self.http('/api/session')[0] == 200:
                        return child
                except (OSError, TimeoutError):
                    pass
            time.sleep(.1)
        self.fail('Isolated export Workbench did not start')

    def prepare_stage(self, rows=1007):
        self.source_table = self.table + '_source'
        self.sql('CREATE TABLE ' + _harness.warehouse.identifier(self.table) +
                 ' (id BIGINT, note VARCHAR)')
        statement = """CREATE TABLE {table} AS SELECT id,
            CASE mod(id, 4) WHEN 0 THEN '日本語' || chr(10) || '次の行'
            WHEN 1 THEN CAST(NULL AS VARCHAR) WHEN 2 THEN ''
            ELSE 'comma, quote"' END AS note
            FROM UNNEST(sequence(BIGINT '1', BIGINT '{rows}')) AS t(id)"""
        self.sql(statement.format(table=_harness.warehouse.identifier(self.source_table), rows=rows))
        source = self.directory / 'stage'
        source.mkdir()
        definition = dict(name='export_acceptance', directory=str(source), table=self.table,
                          mode='append', format='auto')
        saved = self.request('/api/stages/save', definition)['stage']
        self.stage_name = definition['name']
        self.revision = saved['updated_at']
        self.output = source / 'exports'
        return source

    def export_payload(self, filename):
        return dict(name=self.stage_name, table=self.source_table, file=filename,
                    format=filename.rsplit('.', 1)[1], version=self.revision)

    def submit_export(self, filename):
        submitted = self.request('/api/stages/export', self.export_payload(filename), expected=202)['run']
        run = self.wait_run(submitted['id'])
        self.assertEqual(run['status'], 'success', run)
        self.assertEqual((run['trigger'], run['actor'], run['role']), ('stage', 'alice', 'admin'))
        return run

    def read_export(self, path):
        if path.suffix == '.csv':
            with path.open(newline='', encoding='utf-8') as stream:
                return [dict(id=int(row['id']), note=row['note']) for row in csv.DictReader(stream)]
        if path.suffix == '.jsonl':
            return [json.loads(line) for line in path.read_text().splitlines()]
        import pyarrow.parquet as parquet
        return parquet.read_table(path).to_pylist()

    def test_complete_formats_owner_scan_isolation_and_restart(self):
        child = self.start_workbench()
        source = self.prepare_stage()
        expected = [dict(id=number, note=('日本語\n次の行', None, '', 'comma, quote"')[number % 4])
                    for number in range(1, 1008)]
        runs, original = [], {}
        for format in ('csv', 'jsonl'):
            filename = 'complete.' + format
            run = self.submit_export(filename)
            runs.append(run)
            path = self.output / filename
            rows = sorted(self.read_export(path), key=lambda row: row['id'])
            format_expected = expected if format != 'csv' else [
                dict(row, note='' if row['note'] is None else row['note']) for row in expected]
            self.assertEqual(rows, format_expected)
            self.assertEqual(run['steps'][0]['summary']['row_count'], 1007)
            original[filename] = path.read_bytes()
            self.request('/api/stages/export', self.export_payload(filename), expected=400)
            self.assertEqual(path.read_bytes(), original[filename])
        listing = self.request('/api/stages/exports', {'name': self.stage_name})
        self.assertEqual(Path(listing['directory']), self.output)
        self.assertEqual({item['name'] for item in listing['files']}, set(original))
        self.assertEqual(listing['limits']['max_rows'], 100000)
        self.assertEqual(listing['limits']['max_file_bytes'], 25 * 1024 * 1024)
        self.assertEqual(self.request('/api/stages/files', {'name': self.stage_name})['files'], [])
        for user, status in [(None, 401), ('emily', 403), ('vera', 403), ('bob', 404)]:
            self.request('/api/stages/exports', {'name': self.stage_name}, user=user, expected=status)
            self.request('/api/stages/export', self.export_payload('denied.csv'), user=user, expected=status)
        self.assertEqual({run['id'] for run in self.request('/api/jobs/runs', user='bob')['runs']},
                         {run['id'] for run in runs})
        # A genuine automatic scan must ignore every export, even after files
        # become old enough to meet the normal stable-file threshold.
        timestamp = time.time() - 60
        for path in self.output.iterdir():
            os.utime(path, (timestamp, timestamp))
        scan = self.request('/api/stages/scan', {'name': self.stage_name, 'version': self.revision}, expected=202)['run']
        scanned = self.wait_run(scan['id'])
        self.assertEqual(scanned['status'], 'success')
        self.assertEqual([step['action'] for step in scanned['definition']['steps']], ['stage_scan_empty'])
        self.assertEqual(self.sql('SELECT count(*) FROM ' + _harness.warehouse.identifier(self.table))['rows'], [[0]])
        self.stop_workbench(child)
        child = self.start_workbench()
        for run in runs:
            self.assertEqual(self.request('/api/jobs/detail', {'run_id': run['id']})['run'], run)
        self.assertEqual(len(self.request('/api/jobs/runs')['runs']), 3)
        for filename, content in original.items():
            self.request('/api/stages/export', self.export_payload(filename), expected=400)
            self.assertEqual((self.output / filename).read_bytes(), content)
        self.assertEqual(self.request('/api/stages/files', {'name': self.stage_name})['files'], [])
        self.assertEqual({path.name for path in source.iterdir()}, {'exports'})
        self.stop_workbench(child)

    @unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'Parquet runtime requires optional pyarrow')
    def test_complete_parquet_roundtrip(self):
        child = self.start_workbench()
        self.prepare_stage()
        run = self.submit_export('complete.parquet')
        rows = sorted(self.read_export(self.output / 'complete.parquet'), key=lambda row: row['id'])
        expected = [dict(id=number, note=('日本語\n次の行', None, '', 'comma, quote"')[number % 4])
                    for number in range(1, 1008)]
        self.assertEqual(rows, expected)
        self.assertEqual(run['steps'][0]['summary']['row_count'], 1007)
        self.stop_workbench(child)

    def test_published_active_export_restart_unknown_and_no_replay(self):
        child = self.start_workbench(hold_after_export=True)
        self.prepare_stage(rows=5)
        submitted = self.request('/api/stages/export', self.export_payload('published.jsonl'), expected=202)['run']
        deadline = time.monotonic() + 45
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        self.assertTrue(self.marker.exists(), 'Real export did not publish before bounded wait')
        output = self.output / 'published.jsonl'
        original = output.read_bytes()
        self.assertEqual(len(self.read_export(output)), 5)
        self.assertEqual(self.request('/api/jobs/detail', {'run_id': submitted['id']})['run']['status'], 'running')
        self.request('/api/stages/export', self.export_payload('conflict.csv'), expected=409)
        # Simulate the process exiting between publication and bookkeeping.
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        child = self.start_workbench()
        recovered = self.request('/api/jobs/detail', {'run_id': submitted['id']})['run']
        self.assertEqual(recovered['status'], 'unknown', recovered)
        time.sleep(1)
        self.assertEqual(len(self.request('/api/jobs/runs')['runs']), 1)
        self.assertEqual(output.read_bytes(), original)
        self.request('/api/stages/export', self.export_payload('published.jsonl'), expected=400)
        self.assertEqual(output.read_bytes(), original)
        self.assertFalse((self.output / 'conflict.csv').exists())
        self.assertEqual(self.request('/api/stages/files', {'name': self.stage_name})['files'], [])
        self.assertEqual({item['name'] for item in self.request('/api/stages/exports', {'name': self.stage_name})['files']},
                         {'published.jsonl'})
        self.stop_workbench(child)


if __name__ == '__main__':
    unittest.main()
