"""Offline restore tests: generated SQLite and artifacts only, no SDK/server."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock
import uuid

from tools.local_experiment.mlflow import restore as R

STORE_ID = '12345678-1234-4234-8234-123456789012'
RUNS = ['a' * 32, 'b' * 32]


def fixture(root):
    """Create a fake completed export with both profiles and an unused Default."""
    def directory(path):
        path.mkdir(parents=True, exist_ok=True)
        for p in [path, *path.parents]:
            if p == root.parent:
                break
            os.chmod(p, 0o700)
    backup = root / 'tmp/local-experiment/fake-checks/backup/stopped-store-backup'
    directory(backup / 'store/artifacts')
    old_root = root / R.SOURCE / 'store/artifacts'
    owner = {'schema_version': 1, 'owner': 'shirokuma-local-mlflow', 'store_id': STORE_ID,
             'mlflow_version': R.VERSION, 'backend_uri': 'sqlite:///' + str(old_root.parent / 'tracking.sqlite'),
             'artifact_root': str(old_root), 'url': 'http://127.0.0.1:18550'}
    R.write(backup / 'store.json', R.canonical(owner))
    R.write(backup / 'runtime.json', b'{"pid":1234567,"inert":true}')
    R.write(backup / 'store.lock', b'')
    db = backup / 'store/tracking.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''
      CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY);
      CREATE TABLE workspaces (name TEXT,description TEXT,default_artifact_root TEXT,
                              trace_archival_location TEXT,trace_archival_retention INTEGER);
      CREATE TABLE experiments (experiment_id INTEGER PRIMARY KEY,name TEXT,artifact_location TEXT,lifecycle_stage TEXT);
      CREATE TABLE runs (run_uuid TEXT PRIMARY KEY,experiment_id INTEGER REFERENCES experiments(experiment_id),
                         status TEXT,lifecycle_stage TEXT,artifact_uri TEXT,start_time INTEGER);
      CREATE TABLE metrics (key TEXT,value REAL,timestamp INTEGER,step INTEGER,run_uuid TEXT REFERENCES runs(run_uuid));
      CREATE TABLE latest_metrics (key TEXT,value REAL,run_uuid TEXT REFERENCES runs(run_uuid));
      CREATE TABLE params (key TEXT,value TEXT,run_uuid TEXT REFERENCES runs(run_uuid));
      CREATE TABLE tags (key TEXT,value TEXT,run_uuid TEXT REFERENCES runs(run_uuid));
      CREATE TABLE experiment_tags (key TEXT,value TEXT,experiment_id INTEGER REFERENCES experiments(experiment_id));
      CREATE TABLE registered_models (name TEXT);
      CREATE TABLE secrets (secret_id TEXT PRIMARY KEY,secret_name TEXT);
    ''')
    con.execute(R.SECRETS_TRIGGER_SQL)
    con.execute('INSERT INTO alembic_version VALUES (?)', (R.REVISION,))
    con.execute('INSERT INTO workspaces VALUES (?,?,?,?,?)', tuple(R.DEFAULT_WORKSPACE.values()))
    for key, (name, suffix) in R.EXPERIMENTS.items():
        con.execute('INSERT INTO experiments VALUES (?,?,?,?)', (int(key), name, (old_root / suffix).as_uri(), 'active'))
    for key, run_id in zip(['1', '2'], RUNS):
        folder = R.EXPERIMENTS[key][1]
        artifacts = backup / 'store/artifacts' / folder / run_id / 'artifacts'
        directory(artifacts)
        source_id = 'catalog/' + run_id if key == '1' else 'spark-benchmark/' + run_id + '/spark/q1_sums'
        source_key = R.digest(('source identity ' + source_id).encode())
        content = R.canonical({'schema_version': 1, 'source_id': source_id,
                               'source_key': source_key, 'safe': True, 'count': int(key)}) + b'\n'
        R.write(artifacts / 'safe-projection.json', content)
        uri = (old_root / folder / run_id / 'artifacts').as_uri()
        con.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', (run_id, int(key), 'FINISHED', 'active', uri, 1700000000000))
        for step in [0, 1]:
            con.execute('INSERT INTO metrics VALUES (?,?,?,?,?)', ('known_tokens', 10.5 + step, 1700000000000 + step, step, run_id))
        con.execute('INSERT INTO latest_metrics VALUES (?,?,?)', ('known_tokens', 11.5, run_id))
        con.execute('INSERT INTO params VALUES (?,?,?)', ('scope', 'saved-only', run_id))
        con.execute('INSERT INTO tags VALUES (?,?,?)', ('cost', 'unknown', run_id))
        for tag, value in [('shirokuma.source_id', source_id), ('shirokuma.source_key', source_key),
                           ('shirokuma.projection_sha256', R.digest(content))]:
            con.execute('INSERT INTO tags VALUES (?,?,?)', (tag, value, run_id))
        con.execute('INSERT INTO experiment_tags VALUES (?,?,?)', ('shirokuma.store_id', STORE_ID, int(key)))
        index = {'schema_version': 1, 'store_id': STORE_ID, 'experiment_id': key, 'experiment_pending': False,
                 'runs': {source_id: {'completed': True, 'projection_sha256': R.digest(content), 'run_id': run_id,
                                       'source_key': source_key, 'started_ms': 1700000000000}}}
        R.write(backup / ('import-index.json' if key == '1' else 'benchmark-import-index.json'), R.canonical(index))
    con.commit()
    con.close()
    os.chmod(db, 0o600)
    manifest = backup.parent / 'backup-manifest.json'
    refresh(root, backup, manifest)
    return manifest, backup


def refresh(root, backup, manifest):
    value = {'result': 'verified', 'observed_at': '2026-09-18T00:00:00+00:00', 'store_id': STORE_ID,
             'source': R.SOURCE, 'backup': str(backup.relative_to(root)), 'inventory': R.inventory(backup)[0]}
    if manifest.exists():
        manifest.write_bytes(R.canonical(value))
    else:
        R.write(manifest, R.canonical(value))
    return value


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.manifest, self.backup = fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def restore(self):
        return R.restore(self.manifest, root=self.root)

    def sql(self, statement, parameters=()):
        with sqlite3.connect(self.backup / 'store/tracking.sqlite') as con:
            con.execute(statement, parameters)
        refresh(self.root, self.backup, self.manifest)

    def result(self):
        return json.loads(next((self.root / R.DESTINATION).glob('*/result.json')).read_bytes())

    def test_complete_copy_rebases_all_uris_only_and_preserves_source(self):
        before = R.inventory(self.backup)
        raw = self.manifest.read_bytes()
        result = self.restore()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(result['database']['artifact_uri_updates'], {'experiments': 3, 'runs': 2})
        self.assertEqual(R.inventory(self.backup), before)
        self.assertEqual(self.manifest.read_bytes(), raw)
        info = R.inspect_completed(result['token'], root=self.root)
        self.assertEqual(info['expected_snapshot'][1]['metrics']['rows'].__len__(), 4)
        self.assertFalse((info['root'] / 'clone/runtime.json').exists())
        self.assertFalse((info['clone_artifacts'] / '0').exists())
        for name, proof in result['database']['tables'].items():
            self.assertEqual(proof['before_sha256'] == proof['after_sha256'], name not in ('runs', 'experiments'))
        for path in info['root'].rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_existing_token_never_reused_or_modified(self):
        identity = uuid.UUID('12345678123442348234123456789012')
        with mock.patch.object(R.uuid, 'uuid4', return_value=identity):
            self.restore()
            before = R.inventory(self.root / R.DESTINATION / identity.hex)
            with self.assertRaises(FileExistsError):
                self.restore()
            self.assertEqual(R.inventory(self.root / R.DESTINATION / identity.hex), before)

    def test_manifest_tampering_is_refused_before_clone_allocation(self):
        original = self.manifest.read_bytes()
        mutations = [lambda x: x.update(result='pending'), lambda x: x.update(source='tmp/other'),
                     lambda x: x.update(backup='../outside'), lambda x: x.update(extra=True),
                     lambda x: x['inventory'].update({'../escape': x['inventory']['store.json']}),
                     lambda x: x['inventory']['store.json'].update(mtime_ns=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = json.loads(original); mutation(data); self.manifest.write_bytes(R.canonical(data))
                with self.assertRaises(R.RestoreError): self.restore()
                self.assertFalse((self.root / R.DESTINATION).exists())

    def test_hash_and_mtime_mismatch_are_refused(self):
        path = self.backup / 'runtime.json'
        old = path.stat()
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_EXPORT_MISMATCH'): self.restore()
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        path.write_bytes(b'x' * old.st_size)
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_EXPORT_MISMATCH'): self.restore()

    def test_symlink_hardlink_and_public_permissions_are_refused(self):
        path = self.backup / 'runtime.json'
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_PRIVATE_PATH_INVALID'): self.restore()
        os.chmod(path, 0o600)
        os.link(path, self.root / 'hardlink')
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_PRIVATE_PATH_INVALID'): self.restore()
        (self.root / 'hardlink').unlink()
        raw = path.read_bytes(); path.unlink(); path.symlink_to(self.manifest)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_PRIVATE_PATH_INVALID'): self.restore()
        self.assertTrue(raw)

    def test_owner_and_database_sidecar_are_refused(self):
        marker = self.backup / 'store.json'
        old = marker.read_bytes(); value = json.loads(old); value['store_id'] = str(uuid.uuid4())
        marker.write_bytes(R.canonical(value)); refresh(self.root, self.backup, self.manifest)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_OWNER_INVALID'): self.restore()
        marker.write_bytes(old)
        R.write(self.backup / 'store/tracking.sqlite-wal', b'')
        refresh(self.root, self.backup, self.manifest)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_DATABASE_SIDECAR'): self.restore()

    def test_external_encoded_escape_query_and_unknown_artifact_uris(self):
        old = self.root / R.SOURCE / 'store/artifacts/evaluations'
        for uri in ['https://example.invalid/secret', 'file://localhost' + str(old),
                    old.as_uri() + '/%2e%2e/other', old.as_uri() + '?x=1',
                    (self.root / 'elsewhere').as_uri(), old.as_uri() + '/./x']:
            with self.subTest(uri=uri):
                self.sql('UPDATE runs SET artifact_uri=? WHERE run_uuid=?', (uri, RUNS[0]))
                with self.assertRaises(R.RestoreError): self.restore()
        self.assertEqual(self.result()['result'], 'failed')

    def test_missing_run_artifacts_refused_but_unused_default_is_valid(self):
        artifact = self.backup / 'store/artifacts/evaluations' / RUNS[0] / 'artifacts/safe-projection.json'
        artifact.unlink()
        refresh(self.root, self.backup, self.manifest)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_ARTIFACT_MISSING'): self.restore()

    def test_registry_schema_and_workspace_expansion_are_refused(self):
        for statement in ["INSERT INTO registered_models VALUES ('unexpected')",
                          "UPDATE alembic_version SET version_num='old'",
                          "UPDATE workspaces SET default_artifact_root='s3://outside'"]:
            with self.subTest(statement=statement):
                original = (self.backup / 'store/tracking.sqlite').read_bytes()
                self.sql(statement)
                with self.assertRaises(R.RestoreError): self.restore()
                (self.backup / 'store/tracking.sqlite').write_bytes(original)
                refresh(self.root, self.backup, self.manifest)

    def test_triggers_rejected_before_any_update(self):
        self.sql("CREATE TRIGGER tamper AFTER UPDATE ON runs BEGIN DELETE FROM metrics; END")
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_SCHEMA_UNSUPPORTED'): self.restore()
        with sqlite3.connect(self.backup / 'store/tracking.sqlite') as con:
            self.assertEqual(con.execute('SELECT count(*) FROM metrics').fetchone(), (4,))

    def test_pinned_empty_secrets_trigger_is_retained_and_never_fired(self):
        result = self.restore()
        checked = R.inspect_completed(result['token'], root=self.root)
        schema, tables = checked['expected_snapshot']
        self.assertIn(('trigger', 'prevent_secrets_aad_mutation', 'secrets', R.SECRETS_TRIGGER_SQL), schema)
        self.assertEqual(tables['secrets']['rows'], [])
        self.assertEqual(len(tables['metrics']['rows']), 4)
        self.assertEqual(result['database']['tables']['secrets']['before_sha256'],
                         result['database']['tables']['secrets']['after_sha256'])

    def test_altered_known_trigger_and_nonempty_secrets_are_rejected(self):
        self.sql('DROP TRIGGER prevent_secrets_aad_mutation')
        self.sql(R.SECRETS_TRIGGER_SQL.replace("SELECT RAISE(ABORT, 'secret_id and secret_name are immutable (used as AAD in encryption)');",
                                             'DELETE FROM metrics;'))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_SCHEMA_UNSUPPORTED'): self.restore()
        self.sql('DROP TRIGGER prevent_secrets_aad_mutation')
        self.sql(R.SECRETS_TRIGGER_SQL)
        self.sql("INSERT INTO secrets VALUES ('unsupported', 'unsupported')")
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_PROFILE_UNSUPPORTED'): self.restore()

    def test_bad_foreign_key_and_unfinished_run_are_refused(self):
        self.sql('UPDATE metrics SET run_uuid=?', ('c' * 32,))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_DATABASE_INVALID'): self.restore()
        self.sql('UPDATE metrics SET run_uuid=?', (RUNS[0],))
        self.sql("UPDATE runs SET status='RUNNING'")
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_PROFILE_UNSUPPORTED'): self.restore()

    def test_index_completion_and_run_set_are_required(self):
        path = self.backup / 'import-index.json'; original = path.read_bytes()
        for change in ['completion', 'identity']:
            value = json.loads(original); entry = next(iter(value['runs'].values()))
            entry['completed' if change == 'completion' else 'run_id'] = False if change == 'completion' else 'f' * 32
            path.write_bytes(R.canonical(value)); refresh(self.root, self.backup, self.manifest)
            with self.assertRaisesRegex(R.RestoreError, 'RESTORE_INDEX_INVALID'): self.restore()

    def test_source_id_and_source_key_are_distinct_but_bound_to_artifact_and_tags(self):
        index = json.loads((self.backup / 'import-index.json').read_bytes())
        source_id, entry = next(iter(index['runs'].items()))
        self.assertNotEqual(source_id, entry['source_key'])
        self.assertRegex(entry['source_key'], r'^[0-9a-f]{64}$')
        self.assertEqual(self.restore()['result'], 'verified')
        self.sql("UPDATE tags SET value='catalog/forged' WHERE key='shirokuma.source_id' AND run_uuid=?", (RUNS[0],))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_INDEX_INVALID'): self.restore()

    def test_artifact_identity_mismatch_rejected_even_when_all_artifact_hashes_match(self):
        artifact = self.backup / 'store/artifacts/evaluations' / RUNS[0] / 'artifacts/safe-projection.json'
        projection = json.loads(artifact.read_bytes())
        projection['source_key'] = 'f' * 64
        raw = R.canonical(projection) + b'\n'
        artifact.write_bytes(raw)
        index_path = self.backup / 'import-index.json'
        index = json.loads(index_path.read_bytes())
        next(iter(index['runs'].values()))['projection_sha256'] = R.digest(raw)
        index_path.write_bytes(R.canonical(index))
        self.sql("UPDATE tags SET value=? WHERE key='shirokuma.projection_sha256' AND run_uuid=?", (R.digest(raw), RUNS[0]))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_INDEX_INVALID'): self.restore()

    def test_index_start_time_must_match_its_run(self):
        self.sql('UPDATE runs SET start_time=start_time+1 WHERE run_uuid=?', (RUNS[0],))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_INDEX_INVALID'): self.restore()

    def test_source_change_during_copy_keeps_failed_partial_and_never_retries(self):
        original = R.copy_tree
        calls = []
        def changed(*args):
            calls.append(args[0]); result = original(*args)
            if len(calls) == 1:
                (self.backup / 'runtime.json').write_bytes(b'changed')
            return result
        with mock.patch.object(R, 'copy_tree', side_effect=changed):
            with self.assertRaisesRegex(R.RestoreError, 'RESTORE_SOURCE_CHANGED'): self.restore()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.result()['result'], 'failed')

    def test_first_cancel_survives_secondary_receipt_failure(self):
        cancel = KeyboardInterrupt()
        original = R.write
        def fail(path, raw):
            if path.name == 'result.json': raise OSError('private failure text')
            return original(path, raw)
        with mock.patch.object(R, 'copy_tree', side_effect=cancel), mock.patch.object(R, 'write', side_effect=fail):
            with self.assertRaises(KeyboardInterrupt) as caught: self.restore()
        self.assertIs(caught.exception, cancel)
        self.assertEqual(len(list((self.root / R.DESTINATION).iterdir())), 1)

    def test_later_cancel_overrides_ordinary_failure(self):
        cancel = KeyboardInterrupt(); original = R.write
        def fail(path, raw):
            if path.name == 'result.json': raise cancel
            return original(path, raw)
        with mock.patch.object(R, 'copy_tree', side_effect=OSError('fail')), mock.patch.object(R, 'write', side_effect=fail):
            with self.assertRaises(KeyboardInterrupt) as caught: self.restore()
        self.assertIs(caught.exception, cancel)

    def test_database_close_cannot_replace_original_cancel(self):
        cancel = KeyboardInterrupt()
        connection = mock.Mock()
        connection.close.side_effect = OSError('secondary')
        with mock.patch.object(R.sqlite3, 'connect', return_value=connection), \
             mock.patch.object(R, 'database_snapshot', side_effect=cancel):
            with self.assertRaises(KeyboardInterrupt) as caught:
                R.logical_snapshot(self.backup / 'store/tracking.sqlite')
        self.assertIs(caught.exception, cancel)

    def test_completed_clone_tamper_is_not_sdk_ready(self):
        result = self.restore()
        output = self.root / R.DESTINATION / result['token']
        with sqlite3.connect(output / 'clone/store/tracking.sqlite') as con:
            con.execute("UPDATE tags SET value='forged'")
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_DATABASE_CHANGED'):
            R.inspect_completed(result['token'], root=self.root)

    def test_completed_clone_sidecars_are_not_ignored_by_immutable_reader(self):
        result = self.restore()
        database = self.root / result['clone'] / 'store/tracking.sqlite'
        for suffix in ('-wal', '-shm', '-journal'):
            path = Path(str(database) + suffix)
            R.write(path, b'')
            with self.assertRaisesRegex(R.RestoreError, 'RESTORE_DATABASE_SIDECAR'):
                R.logical_snapshot(database)
            path.unlink()

    def test_manifest_metadata_change_during_copy_is_not_success(self):
        original = R.copy_tree
        def changed(*args):
            result = original(*args)
            value = self.manifest.stat()
            os.utime(self.manifest, ns=(value.st_atime_ns, value.st_mtime_ns + 1))
            return result
        with mock.patch.object(R, 'copy_tree', side_effect=changed):
            with self.assertRaisesRegex(R.RestoreError, 'RESTORE_SOURCE_CHANGED'): self.restore()

    def test_sidecar_created_during_read_is_refused_after_close(self):
        database = self.backup / 'store/tracking.sqlite'
        connection = mock.Mock()
        def snapshot(_):
            R.write(Path(str(database) + '-wal'), b'pending')
            return [], {}
        with mock.patch.object(R.sqlite3, 'connect', return_value=connection), \
             mock.patch.object(R, 'database_snapshot', side_effect=snapshot):
            with self.assertRaisesRegex(R.RestoreError, 'RESTORE_DATABASE_SIDECAR'):
                R.logical_snapshot(database)
        connection.close.assert_called_once_with()

    def test_cli_has_fixed_safe_error_and_no_raw_trace(self):
        import contextlib, io
        output = io.StringIO()
        with mock.patch.object(R, 'restore', side_effect=RuntimeError('PRIVATE_ROW')), contextlib.redirect_stdout(output):
            self.assertEqual(R.main(['--manifest', 'tmp/local-experiment/x/backup-manifest.json']), 1)
        self.assertNotIn('PRIVATE_ROW', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'RESTORE_FAILED')


if __name__ == '__main__':
    unittest.main()
