"""Comparison restore using disposable SQLite, pure saved data and fake SDK."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import import_comparisons as I
from tools.local_experiment.mlflow import restore as R
from tools.local_experiment.mlflow import restore_readback as B
from tests.test_local_mlflow_restore import fixture, refresh, STORE_ID
from tests.test_local_mlflow_training_restore import add_training
from tests.test_local_mlflow_training_restore_readback import TrainingSDK
from tests.test_notebook_lab_comparison_saved import COMPARISON_TOKEN, make_saved

RUN, EVIDENCE = '8' * 32, '7' * 32


def comparison_fixture(test):
    """Add a valid baseline and imported candidate to the old three profiles."""
    saved = make_saved(test)
    root = saved.original.root
    manifest, backup = fixture(root)
    add_training(root, backup, keys=('3',))
    source = I.Snapshot(COMPARISON_TOKEN).load()
    receipt = source['source_training']['receipt']
    workload = source['source_training']['workload']
    receipt.update(store_id=STORE_ID, run_id='9' * 32)
    reference = I.T._project(receipt, workload, kind='iceberg')
    source['comparison'] = I.C._project(source['request'], workload, reference,
        source['comparison']['candidate']['model'], source['comparison']['versions'])
    hashes = source['source_files_sha256']
    hashes['training/result.json'] = I.digest(receipt)
    hashes['comparison/worker.stdout.json'] = I.digest(source['comparison'])
    source = I.validate_projection(source)

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in [path.parent, *path.parent.parents]:
            if parent == backup.parent:
                break
            parent.chmod(0o700)
        R.write(path, I.canonical(value))

    old = root / R.SOURCE / 'store/artifacts'
    write(backup / 'lake-training' / receipt['token'] / 'result.json', receipt)
    write(backup / 'lake-training' / receipt['token'] / 'workload.json', workload)
    write(backup / 'store/artifacts/iceberg-training' / receipt['run_id'] / 'artifacts/safe-projection.json', workload)
    write(backup / 'store/artifacts' / I.NAMESPACE / RUN / 'artifacts/safe-projection.json', source)
    entry = dict(source_key=I.digest(dict(comparison_token=COMPARISON_TOKEN,
                 request_sha256=source['comparison']['request_sha256'], model_sha256=source['model_sha256'])),
                 projection_sha256=I.digest(source), run_id=RUN, started_ms=1700000010000,
                 finished_ms=1700000011000, phase='verified', completed=True, evidence_token=EVIDENCE)
    index = dict(schema_version=1, profile=I.PROFILE, store_id=STORE_ID,
                 experiment_id='5', experiment_pending=False, runs={COMPARISON_TOKEN: entry})
    write(backup / I.INDEX_NAME, index)
    params, tags, points = I.expected(source, STORE_ID, entry['started_ms'])
    result = dict(schema_version=1, profile=I.PROFILE, token=EVIDENCE, comparison_token=COMPARISON_TOKEN,
        result='verified', phase='verified', store_id=STORE_ID, experiment_id='5', run_id=RUN,
        import_started_ms=entry['started_ms'] - 100, import_finished_ms=entry['finished_ms'] + 100,
        created_runs=1, added_metrics=18, added_params=len(params), artifact_added=True, source_unchanged=True,
        failure_code=None, source_sha256={name: '1' * 64 for name in I.SOURCE_NAMES},
        projection_sha256=I.digest(source), run_started_ms=entry['started_ms'], run_finished_ms=entry['finished_ms'])
    I.validate_record(result, source, entry, experiment_id='5', store_id=STORE_ID)
    write(backup / I.FOLDER / EVIDENCE / 'source.json', source)
    write(backup / I.FOLDER / EVIDENCE / 'result.json', result)
    with sqlite3.connect(backup / 'store/tracking.sqlite') as db:
        db.execute("ALTER TABLE runs ADD COLUMN user_id TEXT DEFAULT 'tester'")
        for key, name, namespace in [('4', I.T.LAKE_EXPERIMENT, 'iceberg-training'), ('5', I.EXPERIMENT, I.NAMESPACE)]:
            db.execute('INSERT INTO experiments VALUES (?,?,?,?)', (int(key), name, (old / namespace).as_uri(), 'active'))
            for k, v in {'shirokuma.owner': name, 'shirokuma.store_id': STORE_ID}.items():
                db.execute('INSERT INTO experiment_tags VALUES (?,?,?)', (k, v, int(key)))
        baseline_params, baseline_tags, baseline_metrics = I.T._expected(workload, receipt, kind='iceberg')
        baseline_points = [dict(key=k, value=v, timestamp=receipt['training_finished_ms'], step=0)
                           for k, v in baseline_metrics.items()]
        for key, run, namespace, start, end, p, t, metrics in [
            ('4', receipt['run_id'], 'iceberg-training', receipt['started_ms'], receipt['finished_ms'],
             baseline_params, baseline_tags, baseline_points),
            ('5', RUN, I.NAMESPACE, entry['started_ms'], entry['finished_ms'], params, tags, points)]:
            db.execute('INSERT INTO runs (run_uuid,experiment_id,status,lifecycle_stage,artifact_uri,start_time,end_time,name) '
                       'VALUES (?,?,?,?,?,?,?,?)', (run, int(key), 'FINISHED', 'active',
                        (old / namespace / run / 'artifacts').as_uri(), start, end, t['mlflow.runName']))
            for table, values in [('params', p), ('tags', t)]:
                for k, v in values.items():
                    db.execute('INSERT INTO ' + table + ' VALUES (?,?,?)', (k, v, run))
            for point in metrics:
                values = (point['key'], point['value'], point['timestamp'], point['step'], run, 0)
                db.execute('INSERT INTO metrics (key,value,timestamp,step,run_uuid,is_nan) VALUES (?,?,?,?,?,?)', values)
                db.execute('INSERT INTO latest_metrics (key,value,timestamp,step,run_uuid,is_nan) VALUES (?,?,?,?,?,?)', values)
    refresh(root, backup, manifest)
    return dict(root=root, manifest=manifest, backup=backup, source=source, entry=entry, result=result)


class ComparisonRestoreTests(unittest.TestCase):
    def setUp(self):
        value = comparison_fixture(self)
        for key, item in value.items():
            setattr(self, key, item)

    def restore(self):
        return R.restore(self.manifest, root=self.root)

    def rewrite(self, path, value):
        path.write_bytes(I.canonical(value))
        refresh(self.root, self.backup, self.manifest)

    def sql(self, statement, args=()):
        with sqlite3.connect(self.backup / 'store/tracking.sqlite') as db:
            db.execute(statement, args)
        refresh(self.root, self.backup, self.manifest)

    def test_restore_keeps_six_experiments_all_rows_and_safe_artifact_bytes(self):
        before = R.inventory(self.backup)
        result = self.restore()
        self.assertEqual(result['result'], 'verified', result)
        self.assertEqual(result['database']['artifact_uri_updates'], dict(experiments=6, runs=5))
        checked = R.inspect_completed(result['token'], root=self.root)
        artifact = checked['clone_artifacts'] / I.NAMESPACE / RUN / 'artifacts/safe-projection.json'
        self.assertEqual(artifact.read_bytes(), I.canonical(self.source))
        self.assertEqual(R.inventory(self.backup), before)
        self.assertEqual(R.inventory(checked['root'] / 'export')[0], before[0])
        self.assertEqual(set(R.EXPERIMENTS), {'0', '1', '2'})
        self.assertEqual(set(R.TRAINING_EXPERIMENTS), {'3', '4'})

    def test_comparison_pending_index_is_rejected_and_source_preserved(self):
        path = self.backup / I.INDEX_NAME
        index = R.parse(path.read_bytes())
        index['runs'][COMPARISON_TOKEN].update(phase='finish-pending', completed=False)
        self.rewrite(path, index)
        before = R.inventory(self.backup)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_COMPARISON_INVALID'):
            self.restore()
        self.assertEqual(R.inventory(self.backup), before)

    def test_extra_unindexed_run_and_missing_receipt_are_rejected(self):
        path = self.backup / I.INDEX_NAME
        index = R.parse(path.read_bytes())
        original = deepcopy(index)
        index['runs'] = {}
        self.rewrite(path, index)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_COMPARISON_INVALID'):
            self.restore()
        self.rewrite(path, original)
        (self.backup / I.FOLDER / EVIDENCE / 'result.json').unlink()
        refresh(self.root, self.backup, self.manifest)
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_COMPARISON_INVALID'):
            self.restore()

    def test_recorded_times_and_original_training_reference_are_bound(self):
        path = self.backup / I.FOLDER / EVIDENCE / 'result.json'
        self.rewrite(path, dict(self.result, import_started_ms=self.entry['finished_ms'] + 1))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_COMPARISON_INVALID'):
            self.restore()
        self.rewrite(path, self.result)
        path = self.backup / 'lake-training' / self.source['source_training']['receipt']['token'] / 'workload.json'
        raw = path.read_bytes()
        path.write_bytes(raw + b' ')
        refresh(self.root, self.backup, self.manifest)
        with self.assertRaises(R.RestoreError):
            self.restore()

    def test_params_tags_metric_history_and_timestamps_cannot_drift(self):
        for table, column, value in [('params', 'value', 'wrong'), ('tags', 'value', 'wrong'),
                                     ('metrics', 'timestamp', 1), ('latest_metrics', 'step', 1)]:
            with self.subTest(table=table):
                with sqlite3.connect(self.backup / 'store/tracking.sqlite') as db:
                    old = db.execute('SELECT rowid,' + column + ' FROM ' + table + ' WHERE run_uuid=? LIMIT 1', (RUN,)).fetchone()
                self.sql('UPDATE ' + table + ' SET ' + column + '=? WHERE rowid=?', (value, old[0]))
                with self.assertRaisesRegex(R.RestoreError, 'RESTORE_COMPARISON_INVALID'):
                    self.restore()
                self.sql('UPDATE ' + table + ' SET ' + column + '=? WHERE rowid=?', (old[1], old[0]))

    def test_comparison_duplicate_metric_history_is_rejected(self):
        self.sql('INSERT INTO metrics SELECT * FROM metrics WHERE run_uuid=? LIMIT 1', (RUN,))
        with self.assertRaisesRegex(R.RestoreError, 'RESTORE_COMPARISON_INVALID'):
            self.restore()

    def test_sdk_reads_all_six_experiments_and_candidate_history_without_mutation(self):
        result = self.restore()
        checked = R.inspect_completed(result['token'], root=self.root)
        expected = B.expected_view(checked)
        sdk = TrainingSDK(expected)
        sdk.base = checked['clone_artifacts']
        before = B.seal(checked)
        with patch.dict('sys.modules', {'mlflow': None}):
            value = B.sdk_read(checked, expected, factory=sdk.factory)
        self.assertEqual(value['counts'], dict(experiments=6, runs=5, metrics=40, artifacts=5))
        self.assertEqual(sdk.searched_experiments, {'0', '1', '2', '3', '4', '5'})
        self.assertEqual(len(expected['runs'][RUN]['histories']), 18)
        self.assertIn(('download', RUN, ''), sdk.calls)
        self.assertEqual(B.seal(checked), before)

    def test_sdk_cannot_omit_candidate_or_change_its_params_and_history(self):
        result = self.restore()
        checked = R.inspect_completed(result['token'], root=self.root)
        expected = B.expected_view(checked)
        sdk = TrainingSDK(expected)
        sdk.base = checked['clone_artifacts']
        search = sdk.search_runs
        with patch.object(sdk, 'search_runs', side_effect=lambda *a, **kw: [r for r in search(*a, **kw) if r.info.run_id != RUN]):
            with self.assertRaisesRegex(B.ReadbackError, 'RUN_MISMATCH'):
                B.sdk_read(checked, expected, factory=sdk.factory)
        sdk.expected['runs'][RUN]['params']['alpha'] = '1.0'
        with self.assertRaisesRegex(B.ReadbackError, 'DATA_MISMATCH'):
            B.sdk_read(checked, expected, factory=sdk.factory)


if __name__ == '__main__':
    unittest.main()
