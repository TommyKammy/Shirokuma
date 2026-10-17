import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
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

    def query(self, url, sql, headers, max_rows):
        self.assertGreater(max_rows, 1000)
        if sql == 'SHOW SCHEMAS FROM iceberg':
            rows = [[s] for s in ('marts', 'raw', 'sandbox', 'staging', 'information_schema')]
        elif sql.startswith('SHOW CREATE SCHEMA'):
            rows = [['CREATE SCHEMA ' + sql.removeprefix('SHOW CREATE SCHEMA ')]]
        elif sql.startswith('SELECT table_name'):
            rows = [['orders/"2026', 'BASE TABLE'], ['summary', 'VIEW']]
        elif sql.startswith('SHOW CREATE TABLE'):
            rows = [['CREATE TABLE ' + sql.removeprefix('SHOW CREATE TABLE ') + ' (id bigint)']]
        elif sql.startswith('SHOW CREATE VIEW'):
            rows = [['CREATE VIEW ' + sql.removeprefix('SHOW CREATE VIEW ') + ' AS SELECT 1']]
        elif sql.startswith('SELECT *'):
            rows = [[n, None, '日本語'] for n in range(1205)]
        else:
            self.fail('Unexpected SQL: ' + sql)
        return dict(rows=rows, columns=[dict(name='id', type='bigint')], truncated=False)

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

    def test_no_tables_and_missing_state(self):
        (self.state / 'ai_config.json').unlink()
        result = self.create(include_tables=False)
        self.assertFalse(any(name.startswith('data/') for name in result['files']))
        self.assertNotIn('state/ai_config.json', result['files'])
        self.assertFalse(any(c.args[1].startswith('SELECT *') for c in self.execute.call_args_list))

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
