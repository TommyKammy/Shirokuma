"""Opt-in Stage acceptance through a real Workbench and resident TLS/Iceberg.

Set SHIROKUMA_RUNTIME_TESTS=1 and the existing backend credential/CA variables.
Only temporary source/state directories and uniquely named synthetic tables are
used. Content already attempted is blocked unless failed/unknown and explicitly retried.
"""
import base64
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import trino_auth
import warehouse_resident as warehouse


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for dedicated stages acceptance')
class RuntimeStagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not trino_auth.password_mode():
            raise RuntimeError('Runtime stages acceptance requires password-mode Trino')
        trino_auth.load_credentials()
        trino_auth.backend_ssl_context()
        cls.tunnel = warehouse.persistent_trino()
        cls.url = cls.tunnel.__enter__()
        cls.addClassCleanup(cls.tunnel.__exit__, None, None, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.directory = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.directory / 'state'),
                                 SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE='')
        environment.start()
        self.addCleanup(environment.stop)
        self.prefix = 'acceptance_stages_' + uuid.uuid4().hex[:16]
        self.table = 'iceberg.raw.' + self.prefix
        self.addCleanup(self.cleanup_tables)
        self.sql('CREATE SCHEMA IF NOT EXISTS iceberg.raw')
        accounts = {}
        for user, role in [('alice', 'admin'), ('bob', 'admin'),
                           ('emily', 'data_engineer'), ('vera', 'bi_viewer')]:
            salt = os.urandom(16).hex()
            accounts[user] = dict(role=role, salt=salt,
                                  password_hash=auth.password_hash('test-' + user, salt))
        self.accounts = self.directory / 'accounts.json'
        self.accounts.write_text(json.dumps(accounts))

    def sql(self, statement):
        with auth.effective_role('admin'):
            return warehouse.execute(self.url, statement, {
                'X-Trino-User': 'shirokuma-runtime-stages',
                'X-Trino-Catalog': 'iceberg', 'X-Trino-Schema': 'raw'})

    def cleanup_tables(self):
        rows = self.sql('SELECT table_name FROM iceberg.information_schema.tables '
                        "WHERE table_schema = 'raw' AND table_name LIKE " +
                        warehouse.literal(self.prefix + '%'))['rows']
        for row in rows:
            if row[0].startswith(self.prefix):
                self.sql('DROP TABLE IF EXISTS ' + warehouse.identifier('iceberg.raw.' + row[0]))

    def start_workbench(self, state=None, post_publish_error=False):
        log_path = self.directory / ('http-' + uuid.uuid4().hex + '.log')
        env = {**os.environ, 'SHIROKUMA_AUTH_MODE': 'shared',
               'SHIROKUMA_AUTH_FILE': str(self.accounts),
               'SHIROKUMA_STATE_DIR': str(state or self.directory / 'state')}
        server = Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench_server.py'
        command = [sys.executable, str(server), '--port', '0']
        if post_publish_error:
            # Test-only injection after the actual Iceberg side effect. Nothing
            # in the production server accepts a crash/failure environment knob.
            launcher = ("import sys,runpy; sys.path.insert(0," + repr(str(server.parent)) + "); "
                        "import lake_ingest; original=lake_ingest.ingest\n"
                        "def publish_then_fail(*args, **kwargs):\n"
                        "    original(*args, **kwargs)\n"
                        "    raise RuntimeError('injected after real Iceberg publication')\n"
                        "lake_ingest.ingest=publish_then_fail\n"
                        "sys.argv=[" + repr(str(server)) + ",'--port','0']; "
                        "runpy.run_path(" + repr(str(server)) + ",run_name='__main__')")
            command = [sys.executable, '-c', launcher]
        with log_path.open('w') as log:
            child = subprocess.Popen(command, env=env,
                stdout=log, stderr=log, start_new_session=True)
        self.addCleanup(self.stop_workbench, child)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if child.poll() is not None:
                self.fail('Isolated Stage Workbench exited before accepting requests')
            match = re.search(r'Shirokuma Workbench: (http://127\.0\.0\.1:\d+)', log_path.read_text())
            if match:
                self.http_url = match.group(1)
                try:
                    if self.http('/api/session')[0] == 200:
                        return child
                except (OSError, TimeoutError):
                    pass
            time.sleep(.1)
        self.fail('Isolated Stage Workbench did not start within 30 seconds')

    @staticmethod
    def stop_workbench(child):
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)

    def http(self, path, body=None, user='alice'):
        headers = {'Content-Type': 'application/json', 'Origin': self.http_url}
        if user:
            token = base64.b64encode((user + ':test-' + user).encode()).decode()
            headers['Authorization'] = 'Basic ' + token
        request = urllib.request.Request(self.http_url + path, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def request(self, path, body=None, user='alice', expected=200):
        code, result = self.http(path, body, user)
        self.assertEqual(code, expected, result)
        return result

    def wait_run(self, run_id):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            run = self.request('/api/jobs/detail', {'run_id': run_id})['run']
            if run['status'] not in {'queued', 'running'}:
                return run
            time.sleep(.1)
        self.fail('Synthetic stage import did not reach a terminal state')

    def test_http_stage_owner_import_restart_and_ledger_restore(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.table) +
                 ' (id BIGINT, code VARCHAR, amount DECIMAL(10,2))')
        source = self.directory / 'incoming'
        source.mkdir()
        data = source / 'orders.csv'
        data.write_text('id,code,amount\n1,00123,12.30\n2,00007,0.05\n')
        # Only direct supported files appear as selectable stage inputs.
        (source / 'notes.txt').write_text('not a supported input')
        (source / 'nested').mkdir()
        (source / 'nested' / 'hidden.csv').write_text('id\n99\n')
        child = self.start_workbench()
        definition = dict(name='synthetic_orders', directory=str(source), table=self.table,
                          mode='append', format='auto')
        saved = self.request('/api/stages/save', definition)['stage']
        self.assertEqual((saved['owner'], saved['can_manage']), ('alice', True))
        self.assertEqual(saved['definition'], definition)
        listed = self.request('/api/stages', user='bob')['stages']
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0]['can_manage'])
        self.request('/api/stages/save', definition, user='bob', expected=403)
        saved = self.request('/api/stages/save', definition)['stage']

        files = self.request('/api/stages/files', {'name': definition['name']})['files']
        self.assertEqual([item['name'] for item in files], ['orders.csv'])
        selected = files[0]
        self.assertEqual(selected['size'], data.stat().st_size)
        self.assertTrue(selected['modified_at'])
        self.assertTrue(selected['version'])
        self.assertTrue(selected['available'])
        selection = dict(name=definition['name'], file='orders.csv', version=selected['version'])
        preview = self.request('/api/stages/preview', selection)
        self.assertEqual((preview['action'], preview['file'], preview['row_count']),
                         ('preview', 'orders.csv', 2))
        self.assertEqual(preview['version'], selected['version'])
        types = {column['name']: column['type'].upper() for column in preview['columns']}
        self.assertEqual(types['code'], 'VARCHAR')
        self.assertEqual(types['amount'], 'DOUBLE')

        for user, status in [(None, 401), ('emily', 403), ('vera', 403)]:
            self.request('/api/stages', user=user, expected=status)
            for path, body in [('/api/stages/save', definition),
                               ('/api/stages/files', {'name': definition['name']}),
                               ('/api/stages/preview', selection),
                               ('/api/stages/import', selection)]:
                self.request(path, body, user=user, expected=status)
        for path in ('/api/stages/files', '/api/stages/preview', '/api/stages/import'):
            body = {'name': definition['name']} if path.endswith('/files') else selection
            self.request(path, body, user='bob', expected=404)
        for path in ('/api/stages/preview', '/api/stages/import'):
            self.request(path, dict(selection, table=self.table + '_override'), expected=400)

        # A preview's stat version must be refreshed after the source changes.
        data.write_text(data.read_text() + '3,00042,8.75\n')
        self.request('/api/stages/preview', selection, expected=400)
        self.request('/api/stages/import', selection, expected=400)
        self.assertEqual(self.sql('SELECT count(*) FROM ' + warehouse.identifier(self.table))['rows'], [[0]])
        selected = self.request('/api/stages/files', {'name': definition['name']})['files'][0]
        self.assertNotEqual(selected['version'], selection['version'])
        selection['version'] = selected['version']
        self.assertEqual(self.request('/api/stages/preview', selection)['row_count'], 3)
        submitted = self.request('/api/stages/import', selection, expected=202)['run']
        run = self.wait_run(submitted['id'])
        self.assertEqual(run['status'], 'success', run)
        self.assertEqual((run['trigger'], run['job_name'], run['actor'], run['role']),
                         ('stage', 'stage:' + definition['name'], 'alice', 'admin'))
        self.assertEqual(run['definition']['source']['version'], selection['version'])
        runs = [run]
        self.request('/api/stages/import', selection, expected=409)
        self.assertEqual(self.request('/api/jobs')['jobs'], [])
        actual = self.sql('SELECT id, code, CAST(amount AS VARCHAR) FROM ' +
                          warehouse.identifier(self.table) + ' ORDER BY id')['rows']
        self.assertEqual(actual, [[1, '00123', '12.30'],
                                 [2, '00007', '0.05'], [3, '00042', '8.75']])
        columns = self.sql('DESCRIBE ' + warehouse.identifier(self.table))['rows']
        self.assertEqual(next(row[1] for row in columns if row[0] == 'code'), 'varchar')
        self.assertTrue(next(row[1] for row in columns if row[0] == 'amount').startswith('decimal('))

        self.stop_workbench(child)
        child = self.start_workbench()
        self.assertEqual(self.request('/api/stages')['stages'], [saved])
        self.assertEqual({run['id'] for run in self.request('/api/jobs/runs')['runs']},
                         {run['id'] for run in runs})
        for run in runs:
            restored = self.request('/api/jobs/detail', {'run_id': run['id']})['run']
            self.assertEqual(restored, run)

        # Exercise the actual SQLite backup/restore; catalog export is outside
        # this registry/history test and would inspect unrelated resident data.
        import disaster_recovery as dr
        archive = self.directory / 'stage-backup.tar.gz'
        with patch.object(dr, '_catalog', return_value=({}, [])), \
                patch.object(dr, 'MODELS_DIR', self.directory / 'no-models'):
            dr.backup(archive, include_tables=False)
        self.stop_workbench(child)
        recovered = self.directory / 'recovered'
        with patch.object(dr, 'MODELS_DIR', recovered / 'models'):
            dr.restore(archive, target_state_dir=recovered)
        child = self.start_workbench(recovered)
        self.assertEqual(self.request('/api/stages')['stages'], [saved])
        self.assertEqual({run['id'] for run in self.request('/api/jobs/runs')['runs']},
                         {run['id'] for run in runs})
        self.assertEqual(self.request('/api/jobs')['jobs'], [])
        restored_file = self.request('/api/stages/files', {'name': definition['name']})['files'][0]
        self.assertEqual((restored_file['ingestion_status'], restored_file['last_run_id']),
                         ('success', run['id']))
        self.request('/api/stages/import', selection, expected=409)
        data.unlink()
        (source / 'notes.txt').unlink()
        (source / 'nested' / 'hidden.csv').unlink()
        (source / 'nested').rmdir()
        source.rmdir()
        self.request('/api/stages/files', {'name': definition['name']}, expected=400)
        self.request('/api/stages/preview', selection, expected=400)
        self.request('/api/stages/import', selection, expected=400)
        self.assertEqual(len(self.request('/api/jobs/runs')['runs']), 1)
        self.stop_workbench(child)

    def stable_file(self, source, name, body):
        path = source / name
        path.write_text(body)
        timestamp = time.time() - 40
        os.utime(path, (timestamp, timestamp))
        return path

    def stage_scan(self, name):
        stage = next(item for item in self.request('/api/stages')['stages'] if item['name'] == name)
        submitted = self.request('/api/stages/scan', {'name': name, 'version': stage['updated_at']}, expected=202)['run']
        run = self.wait_run(submitted['id'])
        self.assertEqual(run['status'], 'success', run)
        return run

    def stage_count(self):
        return self.sql('SELECT count(*) FROM ' + warehouse.identifier(self.table))['rows'][0][0]

    def wait_schedule(self, name, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            stage = next(stage for stage in self.request('/api/stages')['stages'] if stage['name'] == name)
            if predicate(stage['schedule']):
                return stage['schedule']
            time.sleep(.25)
        self.fail('Stage schedule did not reach expected state')

    def test_http_interval_restart_dedup_pause_revocation_and_restore(self):
        source = self.directory / 'scheduled'
        source.mkdir()
        data = self.stable_file(source, 'first.csv', 'id,code\n1,00123\n')
        self.sql('CREATE TABLE ' + warehouse.identifier(self.table) + ' (id BIGINT, code VARCHAR)')
        child = self.start_workbench()
        name = 'scheduled_orders'
        definition = dict(name=name, directory=str(source), table=self.table, mode='append', format='auto')
        saved = self.request('/api/stages/save', definition)['stage']
        self.assertIsNone(saved['schedule'])
        schedule_payload = dict(name=name, every_minutes=1, enabled=True, version=saved['updated_at'])
        for user, status in [('bob', 404), ('emily', 403), ('vera', 403), (None, 401)]:
            self.request('/api/stages/schedule', schedule_payload, user=user, expected=status)
            self.request('/api/stages/scan', {'name': name, 'version': saved['updated_at']}, user=user, expected=status)
        replace = dict(definition, name='replace_orders', mode='replace')
        replace_saved = self.request('/api/stages/save', replace)['stage']
        self.request('/api/stages/schedule', dict(schedule_payload, name=replace['name'], version=replace_saved['updated_at']), expected=400)
        self.request('/api/stages/scan', {'name': replace['name'], 'version': replace_saved['updated_at']}, expected=400)
        # A second browser tab changing the target invalidates old scan and
        # enable requests before either can operate against the newer target.
        self.request('/api/stages/save', dict(definition, table=self.table + '_changed'))
        self.request('/api/stages/scan', {'name': name, 'version': saved['updated_at']}, expected=400)
        self.request('/api/stages/schedule', schedule_payload, expected=400)
        saved = self.request('/api/stages/save', definition)['stage']
        schedule_payload['version'] = saved['updated_at']
        scheduled = self.request('/api/stages/schedule', schedule_payload)['schedule']
        self.assertTrue(scheduled['enabled'])
        self.assertEqual(scheduled['every_minutes'], 1)
        self.assertEqual(self.request('/api/jobs')['jobs'], [])
        self.stop_workbench(child)
        child = self.start_workbench()
        persisted = next(stage for stage in self.request('/api/stages')['stages'] if stage['name'] == name)['schedule']
        self.assertEqual(persisted['next_run_at'], scheduled['next_run_at'])
        self.assertTrue(persisted['enabled'])
        # Let the real one-minute interval and the normal scheduler poll elapse.
        deadline = time.monotonic() + 90
        run = None
        while time.monotonic() < deadline:
            runs = [item for item in self.request('/api/jobs/runs')['runs']
                    if item['job_name'] == 'stage:' + name and item['trigger'] == 'schedule']
            if runs:
                run = self.wait_run(runs[0]['id'])
                break
            time.sleep(.25)
        self.assertIsNotNone(run, 'Real interval scheduler did not import the stable file')
        self.assertEqual(run['status'], 'success', run)
        self.assertEqual((run['actor'], run['role']), ('alice', 'admin'))
        self.assertEqual(self.stage_count(), 1)
        self.request('/api/stages/schedule', {'name': name, 'enabled': False})
        renamed = source / 'renamed.csv'
        data.rename(renamed)
        self.stable_file(source, 'copy.csv', renamed.read_text())
        os.utime(renamed, None)
        files = self.request('/api/stages/files', {'name': name})['files']
        self.assertEqual({item['ingestion_status'] for item in files}, {'success'})
        self.assertEqual({item['last_run_id'] for item in files}, {run['id']})
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 1)
        self.stable_file(source, 'new.csv', 'id,code\n2,00007\n')
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 2)
        # Same filename with changed content becomes a new eligible identity.
        self.stable_file(source, 'new.csv', 'id,code\n3,00042\n')
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 3)
        self.stable_file(source, 'paused.csv', 'id,code\n4,00004\n')
        before_pause = len(self.request('/api/jobs/runs')['runs'])
        # Cross the next genuine interval: disabling must prevent consumption.
        until = time.monotonic() + 70
        while time.monotonic() < until:
            time.sleep(.5)
        self.assertEqual(len(self.request('/api/jobs/runs')['runs']), before_pause)
        self.assertEqual(self.stage_count(), 3)
        self.assertFalse(self.wait_schedule(name, lambda item: not item['enabled'])['enabled'])

        # Revoked owners must be rechecked at execution, even after restart.
        self.request('/api/stages/schedule', schedule_payload)
        accounts = json.loads(self.accounts.read_text())
        accounts['alice']['role'] = 'bi_viewer'
        self.accounts.write_text(json.dumps(accounts))
        self.stop_workbench(child)
        with sqlite3.connect(self.directory / 'state' / 'jobs.sqlite3') as connection:
            connection.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00+00:00' WHERE name=?",
                               ('stage:' + name,))
        child = self.start_workbench()
        # Bob can inspect schedule state while the revoked owner cannot call it.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            revoked = next(stage for stage in self.request('/api/stages', user='bob')['stages'] if stage['name'] == name)
            if not revoked['schedule']['enabled']:
                break
            time.sleep(.25)
        self.assertFalse(revoked['schedule']['enabled'], revoked)
        revoked_runs = self.request('/api/jobs/runs', user='bob')['runs']
        self.assertEqual(len(revoked_runs), before_pause + 1)
        self.assertEqual((revoked_runs[0]['status'], revoked_runs[0]['actor'], revoked_runs[0]['trigger']),
                         ('skipped', 'alice', 'schedule'))
        self.assertEqual(self.stage_count(), 3)
        accounts['alice']['role'] = 'admin'
        self.accounts.write_text(json.dumps(accounts))
        self.stop_workbench(child)
        child = self.start_workbench()
        self.request('/api/stages/schedule', schedule_payload)
        import disaster_recovery as dr
        archive = self.directory / 'scheduled-backup.tar.gz'
        with patch.object(dr, '_catalog', return_value=({}, [])), \
                patch.object(dr, 'MODELS_DIR', self.directory / 'no-models'):
            dr.backup(archive, include_tables=False)
        self.stop_workbench(child)
        recovered = self.directory / 'schedule-recovered'
        with patch.object(dr, 'MODELS_DIR', recovered / 'models'):
            dr.restore(archive, target_state_dir=recovered)
        child = self.start_workbench(recovered)
        restored = self.wait_schedule(name, lambda item: not item['enabled'])
        self.assertIsNone(restored['next_run_at'])
        statuses = {item['name']: item['ingestion_status'] for item in
                    self.request('/api/stages/files', {'name': name})['files']}
        self.assertEqual(statuses, {'copy.csv': 'success', 'renamed.csv': 'success',
                                   'new.csv': 'success', 'paused.csv': 'new'})
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 4)
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 4)
        self.assertEqual(self.request('/api/jobs')['jobs'], [])
        self.stop_workbench(child)

    def test_scan_upsert_retains_files_beyond_ten_new_stable_inputs(self):
        source = self.directory / 'batch'
        source.mkdir()
        self.sql('CREATE TABLE ' + warehouse.identifier(self.table) + ' (id BIGINT, code VARCHAR)')
        for number in range(1, 12):
            self.stable_file(source, f'{number:02}.csv', f'id,code\n{number},00{number}\n')
        recent = source / 'recent.csv'
        recent.write_text('id,code\n12,0012\n')
        os.utime(recent, (time.time() + 3600, time.time() + 3600))
        self.stable_file(source, 'unfinished.part.csv', 'id,code\n13,0013\n')
        child = self.start_workbench()
        name = 'batch_orders'
        self.request('/api/stages/save', dict(name=name, directory=str(source), table=self.table,
                                             mode='upsert', key='id', format='auto'))
        first = self.stage_scan(name)
        self.assertEqual(len(first['definition']['steps']), 10)
        self.assertEqual(self.stage_count(), 10)
        files = self.request('/api/stages/files', {'name': name})['files']
        statuses = {item['name']: item['ingestion_status'] for item in files}
        self.assertEqual(sum(value == 'success' for value in statuses.values()), 10)
        self.assertEqual(statuses['11.csv'], 'new')
        self.assertEqual(statuses['recent.csv'], 'new')
        self.assertNotIn('unfinished.part.csv', statuses)
        second = self.stage_scan(name)
        self.assertEqual(len(second['definition']['steps']), 1)
        self.assertEqual(self.stage_count(), 11)
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 11)
        self.assertEqual(self.request('/api/jobs')['jobs'], [])
        self.stop_workbench(child)

    def test_real_publication_then_unknown_requires_explicit_retry(self):
        source = self.directory / 'uncertain'
        source.mkdir()
        self.stable_file(source, 'uncertain.csv', 'id,code\n1,00123\n')
        self.sql('CREATE TABLE ' + warehouse.identifier(self.table) + ' (id BIGINT, code VARCHAR)')
        child = self.start_workbench(post_publish_error=True)
        name = 'uncertain_orders'
        self.request('/api/stages/save', dict(name=name, directory=str(source), table=self.table,
                                             mode='append', format='auto'))
        selected = self.request('/api/stages/files', {'name': name})['files'][0]
        selection = dict(name=name, file=selected['name'], version=selected['version'])
        first = self.request('/api/stages/import', selection, expected=202)['run']
        unknown = self.wait_run(first['id'])
        self.assertEqual(unknown['status'], 'unknown', unknown)
        self.assertEqual(self.stage_count(), 1, 'Failure injection must follow real publication')
        self.stop_workbench(child)
        child = self.start_workbench()
        selected = self.request('/api/stages/files', {'name': name})['files'][0]
        self.assertEqual((selected['ingestion_status'], selected['last_run_id']), ('unknown', first['id']))
        self.stage_scan(name)
        self.assertEqual(self.stage_count(), 1)
        self.request('/api/stages/import', selection, expected=409)
        retried = self.request('/api/stages/import', dict(selection, retry=True), expected=202)['run']
        self.assertEqual(self.wait_run(retried['id'])['status'], 'success')
        self.assertEqual(self.stage_count(), 2, 'Explicit retry deliberately accepts duplicate risk')
        self.assertEqual(self.request('/api/stages/files', {'name': name})['files'][0]['ingestion_status'], 'success')
        self.stop_workbench(child)


if __name__ == '__main__':
    unittest.main()
