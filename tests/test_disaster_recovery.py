import contextlib
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import disaster_recovery as dr


class DisasterRecoveryTests(unittest.TestCase):
    def enterContext(self, manager):
        # Ubuntu 22.04 and the host Python predate unittest's 3.11 helper.
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'state'
        self.models = self.root / 'models'
        self.state.mkdir()
        (self.models / 'nested').mkdir(parents=True)
        self.original = {'wap_models.json': b'{"models": ["orders"]}\n',
                         'ai_config.json': b'{"provider":"custom"}\n'}
        for name, data in self.original.items():
            (self.state / name).write_bytes(data)
        (self.models / 'nested/orders.sql').write_bytes(b'SELECT 42 AS answer\n')
        self.enterContext(patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.state)))
        self.enterContext(patch.object(dr, 'MODELS_DIR', self.models))
        self.enterContext(patch.object(dr.warehouse, 'trino',
                                      side_effect=lambda: contextlib.nullcontext('http://trino')))
        self.execute = self.enterContext(patch.object(dr.warehouse, 'execute', side_effect=self.query))
        self.archive = self.root / 'backup.tar.gz'

    def query(self, url, sql, headers, max_rows=sys.maxsize):
        self.assertGreater(max_rows, 1000)
        if sql == 'SHOW SCHEMAS FROM iceberg':
            rows = [[s] for s in ('marts', 'raw', 'sandbox', 'staging', 'information_schema')]
        elif sql.startswith('SHOW CREATE SCHEMA'):
            rows = [['CREATE SCHEMA ' + sql.removeprefix('SHOW CREATE SCHEMA ')]]
        elif sql.startswith('SELECT table_name'):
            rows = [['orders/"2026', 'BASE TABLE'], ['summary', 'VIEW']]
        elif sql.startswith('SHOW CREATE TABLE'):
            rows = [['CREATE TABLE ' + sql.removeprefix('SHOW CREATE TABLE ') +
                     ' (id bigint, missing varchar, label varchar)']]
        elif sql.startswith('SHOW CREATE VIEW'):
            rows = [['CREATE VIEW ' + sql.removeprefix('SHOW CREATE VIEW ') + ' AS SELECT 1']]
        elif sql.startswith('SELECT *'):
            rows = [[n, None, "日本語's"] for n in range(1205)]
        elif sql.startswith(('CREATE ', 'INSERT INTO ')):
            rows = []
        else:
            self.fail('Unexpected SQL: ' + sql)
        return dict(rows=rows, columns=[dict(name='id', type='bigint'),
                    dict(name='missing', type='varchar'), dict(name='label', type='varchar')], truncated=False)

    def create(self, include_tables=True):
        return dr.backup(self.archive, include_tables=include_tables)

    def rewrite(self, change):
        with tarfile.open(self.archive, 'r:gz') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive}
        change(files)
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def test_bundle_contents_and_checksums(self):
        result = self.create()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['manifest']['tables']), 8)
        with tarfile.open(self.archive) as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
            self.assertEqual(set(archive.getnames()), set(manifest['checksums']) | {'manifest.json'})
            for name, checksum in manifest['checksums'].items():
                self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), checksum)
            self.assertEqual(len(json.load(archive.extractfile('data/marts/000000.json'))['rows']), 1205)
            for schema in dr.warehouse.WRITABLE_SCHEMAS:
                self.assertIn('catalog/' + schema + '/schema.sql', archive.getnames())

    def test_round_trip_and_dry_run(self):
        self.create()
        target = self.root / 'restored'
        (self.models / 'nested/orders.sql').unlink()
        result = dr.restore(self.archive, target, dry_run=True)
        self.assertFalse(target.exists())
        self.assertFalse((self.models / 'nested/orders.sql').exists())
        self.assertEqual(len(result['restored_files']), 3)
        dr.restore(self.archive, target)
        for name, data in self.original.items():
            self.assertEqual((target / name).read_bytes(), data)
        self.assertEqual((self.models / 'nested/orders.sql').read_bytes(), b'SELECT 42 AS answer\n')
        (self.state / 'ai_config.json').write_bytes(b'changed')
        dr.restore(self.archive)
        self.assertEqual((self.state / 'ai_config.json').read_bytes(), self.original['ai_config.json'])

    def test_job_ledger_snapshot_includes_live_wal_and_restores_to_empty_state(self):
        source = self.state / 'jobs.sqlite3'
        with sqlite3.connect(source) as live:
            live.execute('PRAGMA journal_mode=WAL')
            live.execute('CREATE TABLE executions (id TEXT, status TEXT)')
            live.execute("INSERT INTO executions VALUES ('job-1', 'running')")
            live.commit()
            self.assertTrue(Path(str(source) + '-wal').exists())
            self.create(include_tables=False)
            live.execute("UPDATE executions SET status='success'")
            live.commit()
        target = self.root / 'restored-jobs'
        dr.restore(self.archive, target)
        with sqlite3.connect(target / 'jobs.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT * FROM executions').fetchall(), [('job-1', 'running')])
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual((target / 'jobs.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_restore_does_not_overwrite_existing_job_ledger(self):
        with sqlite3.connect(self.state / 'jobs.sqlite3') as db:
            db.execute('CREATE TABLE executions (id INTEGER)')
            db.execute('INSERT INTO executions VALUES (42)')
        self.create(include_tables=False)
        (self.state / 'ai_config.json').write_bytes(b'current configuration')
        with self.assertRaisesRegex(dr.BackupError, 'empty state directory'):
            dr.restore(self.archive)
        self.assertEqual((self.state / 'ai_config.json').read_bytes(), b'current configuration')
        with sqlite3.connect(self.state / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('SELECT id FROM executions').fetchone()[0], 42)

    def test_restored_schedules_are_paused_and_original_is_unchanged(self):
        import jobs_resident as jobs
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.state), SHIROKUMA_AUTH_MODE='local'):
            jobs.save_job(dict(name='daily', steps=[dict(action='sql', sql='SELECT 1', mode='read-only')]), 'local', 'admin')
            jobs.save_schedule(dict(name='daily', kind='interval', every_minutes=60, arguments={}, enabled=True), 'local', 'admin')
            self.create(include_tables=False)
            target = self.root / 'restored-schedules'
            dr.restore(self.archive, target)
            self.assertTrue(jobs.list_schedules('local', 'admin')[0]['enabled'])
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(target), SHIROKUMA_AUTH_MODE='local'):
            restored = jobs.list_schedules('local', 'admin')[0]
            self.assertFalse(restored['enabled'])
            self.assertIsNone(restored['next_run_at'])
            self.assertTrue(all(not values for values in jobs.tick().values()))
            self.assertEqual(jobs.list_runs('local', 'admin'), [])

    def test_alert_ledger_round_trip_preserves_acknowledgement_and_identity(self):
        import jobs_resident as jobs
        import alerts_resident as alerts
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''), \
             patch.object(jobs.wr, 'query', return_value=dict(result='verified', rows=[[True]],
                 columns=[dict(name='condition', type='boolean')], truncated=False)), \
             patch.object(jobs.wr, 'telemetry', return_value='skipped'):
            jobs.save_job(dict(name='quality_check', steps=[dict(action='alert', sql='SELECT TRUE',
                message='要確認', severity='critical')]), 'local', 'admin')
            run = jobs.submit_run('quality_check', {}, 'local', 'admin')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                run = jobs.get_run(run['id'], 'local', 'admin')
                if run['status'] not in jobs.ACTIVE:
                    break
                time.sleep(.01)
            for thread in threading.enumerate():
                if thread.name == 'job-' + run['id'][:8]:
                    thread.join(2)
            self.assertEqual(run['status'], 'success', run)
            alert = alerts.list_alerts('local', 'admin')['alerts'][0]
            alerts.acknowledge(alert['id'], 'local', 'admin')
            before = alerts.list_alerts('local', 'admin')
            self.assertEqual(before['unacknowledged_count'], 0)
            self.create(include_tables=False)
            target = self.root / 'restored_alerts'
            dr.restore(self.archive, target_state_dir=target)
            with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(target)):
                self.assertEqual(alerts.list_alerts('local', 'admin'), before)
                self.assertEqual(jobs.get_run(run['id'], 'local', 'admin')['steps'][0]['summary']['alert_id'], alert['id'])

    def test_invalid_job_ledger_is_rejected_before_any_restore_writes(self):
        # Use a checksummed archive to exercise restore validation, not backup.
        self.create(include_tables=False)
        def add_invalid_ledger(files):
            data = b'not a sqlite database'
            files['state/jobs.sqlite3'] = data
            manifest = json.loads(files['manifest.json'])
            manifest['checksums']['state/jobs.sqlite3'] = hashlib.sha256(data).hexdigest()
            files['manifest.json'] = json.dumps(manifest).encode()
        self.rewrite(add_invalid_ledger)
        target = self.root / 'invalid-jobs'
        with self.assertRaisesRegex(dr.BackupError, 'Invalid job ledger'):
            dr.restore(self.archive, target)
        self.assertFalse(target.exists())

    def test_restore_preserves_job_ledger_created_after_preflight(self):
        with sqlite3.connect(self.state / 'jobs.sqlite3') as db:
            db.execute('CREATE TABLE executions (id INTEGER)')
        self.create(include_tables=False)
        target = self.root / 'new-state'
        link = os.link

        def concurrent_creation(source, destination):
            destination.write_bytes(b'newly created ledger')
            return link(source, destination)

        with patch.object(dr.os, 'link', side_effect=concurrent_creation):
            with self.assertRaisesRegex(dr.BackupError, 'appeared during restore'):
                dr.restore(self.archive, target)
        self.assertEqual((target / 'jobs.sqlite3').read_bytes(), b'newly created ledger')

    def test_no_tables_and_missing_state(self):
        (self.state / 'ai_config.json').unlink()
        result = self.create(include_tables=False)
        self.assertFalse(any(name.startswith('data/') for name in result['files']))
        self.assertNotIn('state/ai_config.json', result['files'])
        self.assertFalse(any(c.args[1].startswith('SELECT *') for c in self.execute.call_args_list))

    def test_dbt_yaml_round_trip(self):
        contents = {'nested/schema.yml': b'version: 2\nmodels: []\n',
                    'sources.yaml': b'version: 2\nsources: []\n'}
        for name, data in contents.items():
            (self.models / name).write_bytes(data)
        result = self.create()
        for name in contents:
            self.assertIn('models/' + name, result['files'])
            (self.models / name).unlink()
        dr.restore(self.archive)
        for name, data in contents.items():
            self.assertEqual((self.models / name).read_bytes(), data)

    def test_complex_data_restore(self):
        types = ['array(bigint)', 'array(array(varchar))', 'map(varchar, array(bigint))',
                 'row(label varchar, count bigint)', 'array(bigint)', 'array(bigint)']
        row = [[1, None, 3], [["it's"], []], {'items': [2, None]}, ['ok', 4], [], None]
        payload = dr._json(dict(columns=[dict(name='c' + str(i), type=t)
                                        for i, t in enumerate(types)], rows=[row]))
        files = {'table.sql': b'CREATE TABLE iceberg.raw.complex (c0 array(bigint))',
                 'data.json': payload}
        manifest = {'tables': [dict(catalog='iceberg', schema='raw', table='complex',
                                   type='BASE TABLE', ddl='table.sql', data='data.json')]}
        with patch.object(dr, '_validated_files', return_value=(files, manifest)):
            result = dr.restore(self.archive, restore_warehouse=True)
        self.assertEqual(result['rows_restored'], 1)
        insert = self.execute.call_args.args[1]
        self.assertIn('CAST(ARRAY[1, NULL, 3] AS array(bigint))', insert)
        self.assertIn("CAST(ARRAY[CAST(ARRAY[CAST('it''s' AS varchar)] AS array(varchar)), "
                      'CAST(ARRAY[] AS array(varchar))] AS array(array(varchar)))', insert)
        self.assertIn('CAST(json_parse(\'{"items": [2, null]}\') AS map(varchar, array(bigint)))', insert)
        self.assertIn('CAST(json_parse(\'["ok", 4]\') AS row(label varchar, count bigint))', insert)
        self.assertIn('CAST(ARRAY[] AS array(bigint)), NULL)', insert)

    def test_temporal_row_and_map_restore_rejected_before_any_writes(self):
        types = [
            'row(day date)', 'row(created timestamp(6))',
            'ROW(created TIMESTAMP(3) WITH TIME ZONE)',
            'map(varchar, date)', 'map(date, varchar)',
            'map(varchar, timestamp)',
            'array(row(nested map(varchar, array(timestamp(9)))))',
            'row("day" date, "label" varchar)',
            'map(varchar, row("created" timestamp(6) without time zone))',
        ]
        for sql_type in types:
            for rows in ([], [[None]], [[{}]]):
                for dry_run in (False, True):
                    with self.subTest(sql_type=sql_type, rows=rows, dry_run=dry_run):
                        files = {
                            'catalog/raw/schema.sql': b'CREATE SCHEMA iceberg.raw',
                            'safe.sql': b'CREATE TABLE iceberg.raw.safe (id bigint)',
                            'safe.json': dr._json(dict(columns=[dict(name='id', type='bigint')],
                                                       rows=[[1]])),
                            'bad.sql': ('CREATE TABLE iceberg.raw.bad (payload ' + sql_type + ')').encode(),
                            'bad.json': dr._json(dict(columns=[dict(name='payload', type=sql_type)],
                                                      rows=rows)),
                            'state/ai_config.json': b'changed',
                            'models/nested/orders.sql': b'changed',
                        }
                        manifest = {'tables': [dict(catalog='iceberg', schema='raw', table=name,
                                                   type='BASE TABLE', ddl=name + '.sql',
                                                   data=name + '.json') for name in ('safe', 'bad')]}
                        self.execute.reset_mock()
                        with patch.object(dr, '_validated_files', return_value=(files, manifest)), \
                             patch.object(dr.warehouse, 'trino') as trino, \
                             self.assertRaisesRegex(dr.BackupError,
                                 r'Unsupported recovery type for .*bad.*payload.*Trino 483') as error:
                            dr.restore(self.archive, dry_run=dry_run, restore_warehouse=True)
                        self.assertIn(sql_type, str(error.exception))
                        trino.assert_not_called()
                        self.execute.assert_not_called()
                        self.assertEqual((self.state / 'ai_config.json').read_bytes(),
                                         self.original['ai_config.json'])
                        self.assertEqual((self.models / 'nested/orders.sql').read_bytes(),
                                         b'SELECT 42 AS answer\n')

    def test_supported_temporal_arrays_and_temporal_field_names(self):
        for sql_type in ('date', 'timestamp(6)', 'array(date)',
                         'array(array(timestamp(3) with time zone))',
                         'row(date varchar, timestamp bigint)',
                         'row("date" varchar, "timestamp" bigint)',
                         'map(varchar, row("a"" timestamp" varchar))'):
            with self.subTest(sql_type=sql_type):
                dr._validate_restore_type(sql_type, 'table', 'column')
        self.assertEqual(dr._data_literal(['2026-10-18', None], 'array(date)'),
                         "CAST(ARRAY[CAST('2026-10-18' AS date), NULL] AS array(date))")
        self.assertEqual(dr._data_literal(['2026-10-18 12:00:00.123'], 'array(timestamp(3))'),
                         "CAST(ARRAY[CAST('2026-10-18 12:00:00.123' AS timestamp(3))] "
                         'AS array(timestamp(3)))')

    def test_view_dependencies_retry_until_resolved(self):
        files = {name + '.sql': ('CREATE VIEW ' + name + ' AS SELECT 1').encode()
                 for name in ('third', 'second', 'first')}
        manifest = {'tables': [dict(catalog='iceberg', schema='raw', table=name,
                                   type='VIEW', ddl=name + '.sql')
                               for name in ('third', 'second', 'first')]}
        created = []
        def execute(url, sql, headers):
            name = sql.split()[2]
            dependency = {'third': 'second', 'second': 'first'}.get(name)
            if dependency and dependency not in created:
                raise RuntimeError('View not found: ' + dependency)
            created.append(name)
        self.execute.side_effect = execute
        with patch.object(dr, '_validated_files', return_value=(files, manifest)):
            dr.restore(self.archive, restore_warehouse=True)
        self.assertEqual(created, ['first', 'second', 'third'])
        self.execute.side_effect = RuntimeError('Missing dependency')
        self.execute.reset_mock()
        with patch.object(dr, '_validated_files', return_value=(files, manifest)), \
             self.assertRaisesRegex(dr.BackupError, 'Unable to restore dependent views'):
            dr.restore(self.archive, restore_warehouse=True)
        self.assertEqual(self.execute.call_count, 3)

    def test_restore_warehouse_ddl_and_data(self):
        self.create()
        self.execute.reset_mock()
        result = dr.restore(self.archive, self.root / 'restored', restore_warehouse=True)
        statements = [call.args[1] for call in self.execute.call_args_list]
        self.assertEqual(result['restored_schemas'], ['marts', 'raw', 'sandbox', 'staging'])
        self.assertEqual(len(result['restored_tables']), 8)
        self.assertEqual(result['rows_restored'], 4 * 1205)
        self.assertEqual(sum(item['rows'] for item in result['restored_data']), 4 * 1205)
        self.assertEqual(statements[:4], ['CREATE SCHEMA IF NOT EXISTS "iceberg"."' + s + '"'
                                         for s in result['restored_schemas']])
        self.assertEqual(sum(s.startswith('CREATE TABLE ') for s in statements), 4)
        self.assertEqual(sum(s.startswith('CREATE VIEW ') for s in statements), 4)
        inserts = [s for s in statements if s.startswith('INSERT INTO ')]
        self.assertEqual(len(inserts), 12)
        self.assertIn('"iceberg"."marts"."orders/""2026" ("id", "missing", "label")', inserts[0])
        self.assertIn("(0, NULL, CAST('日本語''s' AS varchar))", inserts[0])
        self.assertIn("(1204, NULL, CAST('日本語''s' AS varchar))", inserts[2])

    def test_restore_warehouse_dry_run_and_ddl_only(self):
        for include_tables in (True, False):
            self.create(include_tables=include_tables)
            self.execute.reset_mock()
            target = self.root / 'planned'
            with patch.object(dr.warehouse, 'trino') as trino:
                result = dr.restore(self.archive, target, dry_run=True, restore_warehouse=True)
            trino.assert_not_called()
            self.execute.assert_not_called()
            self.assertFalse(target.exists())
            self.assertEqual(len(result['restored_schemas']), 4)
            self.assertEqual(len(result['restored_tables']), 8)
            self.assertEqual(result['rows_restored'], 4820 if include_tables else 0)
        dr.restore(self.archive, target, restore_warehouse=True)
        self.assertFalse(any(c.args[1].startswith('INSERT INTO ') for c in self.execute.call_args_list))

    def test_cli_restore_warehouse(self):
        self.create()
        self.execute.reset_mock()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = dr.main(['restore', '--input', str(self.archive), '--restore-warehouse',
                            '--target-dir', str(self.root / 'cli-restored')])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())['rows_restored'], 4820)
        self.assertTrue(any(c.args[1].startswith('INSERT INTO ') for c in self.execute.call_args_list))

    def test_corrupted_archive(self):
        self.create()
        data = self.archive.read_bytes()
        for invalid in (b'not gzip', data[:len(data) // 2], data[:-8]):
            self.archive.write_bytes(invalid)
            with self.assertRaises(dr.BackupError):
                dr.restore(self.archive, self.root / 'target')
            self.assertFalse((self.root / 'target').exists())

    def test_checksum_mismatch_before_any_write(self):
        self.create()
        self.rewrite(lambda files: files.__setitem__('catalog/marts/schema.sql', b'corruption'))
        with self.assertRaisesRegex(dr.BackupError, 'Checksum mismatch'):
            dr.restore(self.archive, self.root / 'target')
        self.assertFalse((self.root / 'target').exists())

    def test_inventory_and_unsafe_member_rejected(self):
        for member in ('models/../../escape.sql', '/tmp/escape.sql', 'data/extra.json'):
            self.create()
            self.rewrite(lambda files: files.__setitem__(member, b'bad'))
            with self.assertRaises(dr.BackupError):
                dr.restore(self.archive, self.root / 'target')
        self.assertFalse((self.root / 'target').exists())

    def test_invalid_manifest_and_duplicate_or_link_members(self):
        for invalid in (b'not json', b'null', b'{"version":99,"tables":[]}'):
            self.create()
            self.rewrite(lambda files: files.__setitem__('manifest.json', invalid))
            with self.assertRaises(dr.BackupError):
                dr.restore(self.archive)
        for is_link in (False, True):
            self.create()
            with tarfile.open(self.archive) as archive:
                files = {m.name: archive.extractfile(m).read() for m in archive}
            with tarfile.open(self.archive, 'w:gz') as archive:
                for name, data in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                extra = tarfile.TarInfo('state/wap_models.json')
                if is_link:
                    extra.type, extra.linkname = tarfile.SYMTYPE, '/tmp/outside'
                archive.addfile(extra)
            with self.assertRaises(dr.BackupError):
                dr.restore(self.archive)

    def test_kubectl_environment_configuration(self):
        code = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
                'import warehouse_resident as w; print(json.dumps(w.KUBECTL))')
        for context in (None, '', 'none', 'default', 'linux-cluster'):
            env = dict(os.environ, KUBE_NAMESPACE='custom-namespace')
            env.pop('KUBE_CONTEXT', None)
            if context is not None:
                env['KUBE_CONTEXT'] = context
            result = subprocess.run([sys.executable, '-c', code, str(Path(dr.__file__).parent)],
                                    env=env, text=True, capture_output=True, check=True)
            expected = ['kubectl']
            if context not in ('', 'none', 'default'):
                expected += ['--context', context or 'colima-mac-studio-solo']
            self.assertEqual(json.loads(result.stdout), expected + ['-n', 'custom-namespace'])

    def test_failed_or_truncated_query_does_not_publish(self):
        self.execute.side_effect = RuntimeError('warehouse offline')
        with self.assertRaises(RuntimeError):
            self.create()
        self.assertFalse(self.archive.exists())
        self.execute.side_effect = None
        self.execute.return_value = dict(rows=[], truncated=True)
        with self.assertRaises(dr.BackupError):
            self.create()
        self.assertFalse(self.archive.exists())

    def test_cli_commands(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(dr.main(['backup', '--output', str(self.archive), '--no-tables']), 0)
        self.assertFalse(json.loads(stdout.getvalue())['manifest']['include_tables'])
        target = self.root / 'cli-state'
        command = [sys.executable, str(Path(dr.__file__)), 'restore', '--input', str(self.archive),
                   '--target-dir', str(target), '--dry-run']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['dry_run'])
        self.assertFalse(target.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dr.main(['restore', '--input', str(self.archive),
                                      '--target-dir', str(target)]), 0)
        self.assertEqual((target / 'wap_models.json').read_bytes(), self.original['wap_models.json'])
        self.archive.write_bytes(b'bad')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(dr.main(['restore', '--input', str(self.archive)]), 1)


if __name__ == '__main__':
    unittest.main()
