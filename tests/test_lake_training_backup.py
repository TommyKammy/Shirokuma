"""Stopped-state export tests use only newly created private SQLite fixtures."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import backup as B


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.base = self.root / 'tmp/local-experiment'
        self.base.mkdir(parents=True, mode=0o700)
        self.state = self.base / 'mlflow-tracking'
        self.state.mkdir(mode=0o700)
        self.store = self.state / 'store'
        self.store.mkdir(mode=0o700)
        self.artifacts = self.store / 'artifacts'
        self.artifacts.mkdir(mode=0o700)
        self.database = self.store / 'tracking.sqlite'
        connection = sqlite3.connect(self.database)
        connection.execute('CREATE TABLE preserved(value TEXT)')
        connection.execute("INSERT INTO preserved VALUES ('old row')")
        connection.commit(); connection.close()
        self.database.chmod(0o600)
        self.write(self.state / 'store.json', b'{"store_id":"owned"}\n')
        self.write(self.artifacts / 'retained.json', b'{"retained":true}\n')
        self.parent = self.base / 'new-attempt'
        self.parent.mkdir(mode=0o700)
        self.destination = self.parent / 'cold-backup'
        self.locked = False
        self.calls = []
        @contextmanager
        def lock():
            self.assertFalse(self.locked)
            self.locked = True
            self.calls.append('lock')
            try:
                yield
            finally:
                self.locked = False
        def stopped():
            self.assertTrue(self.locked)
            self.calls.append('stopped')
            return {'state': 'stopped', 'store_id': 'owned'}
        self.runtime = SimpleNamespace(STATE=self.state, STORE=self.store, store_lock=lock, require_stopped=stopped)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, raw):
        path.write_bytes(raw); path.chmod(0o600)

    def export(self):
        return B.export_stopped(self.destination, runtime=self.runtime, root=self.root)

    def test_complete_copy_preserves_source_and_has_exclusive_manifest(self):
        before = B.D.inventory(self.state)
        value = self.export()
        self.assertEqual(value['result'], 'verified')
        self.assertEqual(B.D.inventory(self.state), before)
        self.assertEqual(B.D.inventory(self.destination)[0], before[0])
        self.assertEqual(json.loads((self.parent / 'backup-manifest.json').read_bytes()), value)
        self.assertEqual((self.parent / 'backup-manifest.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls, ['lock', 'stopped', 'stopped'])
        with self.assertRaisesRegex(B.E.ImportError, 'BACKUP_ALREADY_EXISTS'):
            self.export()
        self.assertEqual(B.D.inventory(self.state), before)

    def test_running_store_is_refused_before_copy(self):
        self.runtime.require_stopped = lambda: (_ for _ in ()).throw(RuntimeError('running'))
        with self.assertRaises(RuntimeError): self.export()
        self.assertFalse(self.destination.exists())

    def test_sidecars_even_dangling_links_are_refused(self):
        for suffix in ('-wal', '-journal', '-shm'):
            with self.subTest(suffix=suffix):
                sidecar = self.store / ('tracking.sqlite' + suffix)
                sidecar.symlink_to(self.parent / 'missing')
                with self.assertRaisesRegex(B.E.ImportError, 'BACKUP_ACTIVE_SIDECAR'): self.export()
                self.assertFalse(self.destination.exists())
                sidecar.unlink()

    def test_unsafe_retained_artifact_is_not_copied(self):
        retained = self.artifacts / 'retained.json'
        for mode in (0o644, 0o666):
            with self.subTest(mode=mode):
                retained.chmod(mode)
                with self.assertRaises(B.D.RestoreError): self.export()
                self.assertFalse(self.destination.exists())
        retained.chmod(0o600)
        os.link(retained, self.artifacts / 'alias.json')
        with self.assertRaises(B.D.RestoreError): self.export()

    def test_source_change_during_copy_prevents_verified_manifest(self):
        copy = B.D.copy_tree
        def changed(*args):
            copy(*args)
            self.write(self.artifacts / 'retained.json', b'{"changed":true}\n')
        with patch.object(B.D, 'copy_tree', side_effect=changed):
            with self.assertRaisesRegex(B.E.ImportError, 'BACKUP_SOURCE_CHANGED'): self.export()
        self.assertTrue(self.destination.exists())
        self.assertFalse((self.parent / 'backup-manifest.json').exists())

    def test_corrupt_database_prevents_export(self):
        self.write(self.database, b'not sqlite')
        with self.assertRaises(sqlite3.DatabaseError): self.export()
        self.assertFalse(self.destination.exists())

    def test_store_identity_change_prevents_verified_manifest(self):
        identities = iter([{'store_id': 'owned'}, {'store_id': 'other'}])
        self.runtime.require_stopped = lambda: next(identities)
        with self.assertRaisesRegex(B.E.ImportError, 'BACKUP_STORE_CHANGED'): self.export()
        self.assertFalse((self.parent / 'backup-manifest.json').exists())

    def test_cancellation_keeps_partial_copy_and_original_source(self):
        before = B.D.inventory(self.state)
        original = KeyboardInterrupt()
        def cancelled(source, destination, expected):
            destination.mkdir(mode=0o700)
            raise original
        with patch.object(B.D, 'copy_tree', side_effect=cancelled):
            with self.assertRaises(KeyboardInterrupt) as found: self.export()
        self.assertIs(found.exception, original)
        self.assertEqual(B.D.inventory(self.state), before)
        self.assertTrue(self.destination.exists())
        self.assertFalse((self.parent / 'backup-manifest.json').exists())

    def test_destination_link_and_source_overlap_are_refused(self):
        self.destination.symlink_to(self.state, target_is_directory=True)
        with self.assertRaisesRegex(B.E.ImportError, 'BACKUP_ALREADY_EXISTS'): self.export()
        self.destination.unlink()
        self.destination = self.state / 'cold-backup'
        with self.assertRaisesRegex(B.E.ImportError, 'BACKUP_SOURCE_OVERLAP'): self.export()


if __name__ == '__main__':
    unittest.main()
